"""联合成员变更模拟器核心。

模拟流程：旧配置 C_old -> 联合配置 (C_old, C_new) -> 新配置 C_new。
每轮由领导复制日志、推进提交索引，并验证：
  1. 每个阶段的多数集合（联合配置要求两个集合各自多数）；
  2. 配置日志是否连续（已提交配置必须在领导日志中）；
  3. 旧领导在联合阶段是否仍能提交、退出联合后是否应退位；
  4. 节点离线后 quorum 是否仍然成立。
"""

from .config import MembershipConfig
from .node import LogEntry, Node


class RoundReport:
    """单轮输出：领导、quorum、已提交配置、事件与拒绝原因。"""

    def __init__(self, round_no):
        self.round_no = round_no
        self.term = 0
        self.leader = None
        self.quorum = {}
        self.committed_config = None
        self.events = []
        self.rejections = []

    def line(self):
        quorum = " ".join(
            "%s=%s" % (k, "OK" if v else "LOST") for k, v in self.quorum.items()
        ) or "-"
        parts = [
            "round %d" % self.round_no,
            "term %d" % self.term,
            "leader=%s" % (self.leader or "无"),
            "quorum %s" % quorum,
            "committed=%s" % self.committed_config,
        ]
        if self.events:
            parts.append("events: " + "；".join(self.events))
        if self.rejections:
            parts.append("rejects: " + "；".join(self.rejections))
        return " | ".join(parts)

    def __repr__(self):
        return self.line()


class Simulator:
    def __init__(self, members, term=1):
        members = list(members)
        if len(set(members)) != len(members):
            raise ValueError("初始成员存在重复: %s" % members)
        self.nodes = {m: Node(m) for m in members}
        self.term = term
        self.leader_id = None
        self.committed_config = MembershipConfig(members)
        self._config_committed_once = False
        self.round_no = 0
        self.reports = []
        self._pending_events = []
        self._pending_rejections = []

    def _leader(self):
        if self.leader_id is None:
            return None
        return self.nodes[self.leader_id]

    def _event(self, msg):
        self._pending_events.append(msg)

    def _reject(self, msg):
        self._pending_rejections.append(msg)

    def effective_config(self):
        """当前生效配置：领导日志中最新配置条目，否则为已提交配置。

        领导切换后，若新领导日志中没有未提交的配置条目，生效配置
        自然回退到已提交配置——这就是配置回退。
        """
        leader = self._leader()
        if leader is not None:
            for entry in reversed(leader.log):
                if entry.kind == LogEntry.CONFIG:
                    return entry.config
        return self.committed_config

    def _voter_sets(self, config):
        """多数集合，排除仍在追赶的 learner（learner 无投票权）。"""
        sets = []
        for voters in config.voter_sets():
            sets.append(frozenset(
                v for v in voters
                if v in self.nodes and not self.nodes[v].learner
            ))
        return sets

    @staticmethod
    def _up_to_date(candidate, voter):
        return (candidate.last_term, candidate.last_index) >= (
            voter.last_term, voter.last_index)

    def elect(self, candidate_id):
        """领导选举：候选者必须在生效配置的每个多数集合中分别获胜。"""
        candidate = self.nodes.get(candidate_id)
        if candidate is None:
            self._reject("选举失败：未知节点 %s" % candidate_id)
            return False
        if not candidate.online:
            self._reject("选举失败：候选者 %s 离线" % candidate_id)
            return False
        if candidate.learner:
            self._reject("选举失败：候选者 %s 仍是追赶中的 learner" % candidate_id)
            return False
        effective = self.effective_config()
        if candidate_id not in effective.members():
            self._reject("选举失败：候选者 %s 不在当前配置中" % candidate_id)
            return False
        ok = True
        for voters in self._voter_sets(effective):
            votes = 0
            for vid in sorted(voters):
                voter = self.nodes[vid]
                if voter.online and self._up_to_date(candidate, voter):
                    votes += 1
            if votes <= len(voters) // 2:
                ok = False
                self._reject(
                    "选举失败：%s 在多数集合 {%s} 仅获 %d 票（需 >%d）"
                    % (candidate_id, ",".join(sorted(voters)),
                       votes, len(voters) // 2))
        if not ok:
            return False
        self.term += 1
        self.leader_id = candidate_id
        candidate.term = self.term
        self._event("%s 当选领导（term %d）" % (candidate_id, self.term))
        return True

    def fail(self, node_id):
        self.nodes[node_id].online = False
        self._event("节点 %s 离线" % node_id)

    def recover(self, node_id):
        self.nodes[node_id].online = True
        self._event("节点 %s 恢复上线" % node_id)

    def append(self, payload):
        """追加普通日志条目（仅领导）。"""
        leader = self._leader()
        if leader is None or not leader.online:
            self._reject("追加失败：无可用领导")
            return False
        leader.log.append(LogEntry(
            leader.last_index + 1, self.term, LogEntry.NORMAL, payload=payload))
        return True

    def _config_log_contiguous(self, leader):
        """配置日志连续性：已提交的配置条目必须存在于领导日志中。"""
        if not self._config_committed_once:
            return True
        for entry in leader.log[:leader.commit_index]:
            if entry.kind == LogEntry.CONFIG and \
                    entry.config == self.committed_config:
                return True
        return False

    def propose_config(self, new_members):
        """提议成员变更。返回是否受理，拒绝原因记入当轮报告。"""
        leader = self._leader()
        if leader is None or not leader.online:
            self._reject("配置变更被拒绝：无可用领导")
            return False
        new = frozenset(new_members)
        if not new:
            self._reject("配置变更被拒绝：新配置不能为空")
            return False
        if not self._config_log_contiguous(leader):
            self._reject("配置变更被拒绝：配置日志不连续，"
                         "领导日志缺少已提交的配置条目")
            return False
        uncommitted = [e for e in leader.log
                       if e.kind == LogEntry.CONFIG
                       and e.index > leader.commit_index]
        if uncommitted:
            self._reject(
                "配置变更被拒绝：索引 %d 的配置尚未提交，再次变更可能产生"
                "两个都自认合法的多数集合" % uncommitted[0].index)
            return False
        effective = self.effective_config()
        if effective.is_joint:
            if new != effective.new:
                self._reject(
                    "配置变更被拒绝：联合配置尚未退出，必须先提交新配置，"
                    "而不是 %s" % sorted(new))
                return False
            config = MembershipConfig(sorted(effective.new))
        else:
            if new == effective.old:
                self._reject("配置变更被拒绝：重复配置项，与当前生效配置相同")
                return False
            config = MembershipConfig(effective.old, new)
        for entry in leader.log:
            if entry.kind == LogEntry.CONFIG and entry.config == config:
                self._reject(
                    "配置变更被拒绝：重复配置项，日志索引 %d 已存在相同配置"
                    % entry.index)
                return False
        if not effective.is_joint:
            for m in sorted(new):
                if m not in effective.old and m not in self.nodes:
                    self.nodes[m] = Node(m)
                    self.nodes[m].learner = True
                    self._event("新节点 %s 以 learner 身份加入并追赶" % m)
                elif m not in effective.old and not self.nodes[m].learner:
                    self.nodes[m].learner = True
                    self._event("节点 %s 转为 learner 开始追赶" % m)
        leader.log.append(LogEntry(
            leader.last_index + 1, self.term, LogEntry.CONFIG, config=config))
        if config.is_joint:
            self._event("进入联合配置 %s" % config)
        else:
            self._event("提交新配置条目 %s（退出联合）" % config)
        return True

    def run_round(self):
        """推进一轮：复制日志、追赶 learner、推进提交索引、输出报告。"""
        self.round_no += 1
        report = RoundReport(self.round_no)
        report.term = self.term
        report.events = self._pending_events
        self._pending_events = []
        report.rejections = self._pending_rejections
        self._pending_rejections = []

        leader = self._leader()
        if leader is not None and leader.online:
            self._replicate(leader, report)
            self._advance_commit(leader, report)
            self._maybe_step_down(leader, report)

        if self.leader_id is not None:
            report.leader = self.leader_id
            if not self.nodes[self.leader_id].online:
                report.leader += "(离线)"
        effective = self.effective_config()
        online_voters = set(
            nid for nid, n in self.nodes.items() if n.online and not n.learner)
        for voters in self._voter_sets(effective):
            key = "{" + ",".join(sorted(voters)) + "}"
            report.quorum[key] = (
                len(set(voters) & online_voters) > len(voters) // 2)
        report.committed_config = self.committed_config
        self.reports.append(report)
        return report

    def run_rounds(self, n):
        return [self.run_round() for _ in range(n)]

    def _replicate(self, leader, report):
        for node in self.nodes.values():
            if node.node_id == self.leader_id or not node.online:
                continue
            i = 0
            while i < node.last_index and i < leader.last_index and \
                    node.log[i].term == leader.log[i].term:
                i += 1
            if i < node.last_index:
                dropped = node.log[i:]
                node.log = node.log[:i]
                for entry in dropped:
                    if entry.kind == LogEntry.CONFIG:
                        report.events.append(
                            "节点 %s 回滚未提交配置 %s（索引 %d）"
                            % (node.node_id, entry.config, entry.index))
                report.events.append(
                    "节点 %s 截断 %d 条冲突日志" % (node.node_id, len(dropped)))
            if node.last_index < leader.last_index:
                missing = leader.log[node.last_index:]
                node.log.extend(missing)
                report.events.append(
                    "节点 %s 复制 %d 条日志（追至索引 %d）"
                    % (node.node_id, len(missing), leader.last_index))
            node.commit_index = min(leader.commit_index, node.last_index)
            node.term = self.term
            if node.learner and node.last_index >= leader.last_index:
                node.learner = False
                report.events.append(
                    "节点 %s 已追平日志，转为投票成员" % node.node_id)

    def _advance_commit(self, leader, report):
        effective = self.effective_config()
        voter_sets = self._voter_sets(effective)
        target = leader.commit_index
        for idx in range(leader.commit_index + 1, leader.last_index + 1):
            entry = leader.log[idx - 1]
            if entry.term != self.term:
                continue
            committable = True
            for voters in voter_sets:
                copies = sum(
                    1 for vid in voters
                    if self.nodes[vid].has_entry(idx, entry.term))
                if copies <= len(voters) // 2:
                    committable = False
                    report.events.append(
                        "索引 %d 暂不可提交：多数集合 {%s} 仅 %d 份副本（需 >%d）"
                        % (idx, ",".join(sorted(voters)),
                           copies, len(voters) // 2))
            if committable:
                target = idx
        if target == leader.commit_index:
            return
        old_commit = leader.commit_index
        leader.commit_index = target
        for idx in range(old_commit + 1, target + 1):
            entry = leader.log[idx - 1]
            if entry.kind == LogEntry.CONFIG:
                self.committed_config = entry.config
                self._config_committed_once = True
                if entry.config.is_joint:
                    report.events.append("联合配置已提交：%s" % entry.config)
                else:
                    report.events.append(
                        "新配置已提交，退出联合：%s" % entry.config)

    def _maybe_step_down(self, leader, report):
        """新配置（非联合）提交后，不在其中的旧领导必须退位。"""
        config = self.committed_config
        if config.is_joint:
            return
        if self.leader_id is not None and self.leader_id not in config.old:
            report.events.append(
                "旧领导 %s 不在新配置 %s 中，退位" % (self.leader_id, config))
            self.leader_id = None

    def summary(self):
        return "\n".join(report.line() for report in self.reports)
