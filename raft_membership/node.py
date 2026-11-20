"""日志条目与节点状态。"""


class LogEntry:
    """一条日志：普通条目或配置条目。"""

    CONFIG = "config"
    NORMAL = "normal"

    __slots__ = ("index", "term", "kind", "config", "payload")

    def __init__(self, index, term, kind, config=None, payload=None):
        self.index = index
        self.term = term
        self.kind = kind
        self.config = config
        self.payload = payload

    def __repr__(self):
        if self.kind == self.CONFIG:
            return "LogEntry(#%d,t%d,%s,%s)" % (
                self.index, self.term, self.kind, self.config)
        return "LogEntry(#%d,t%d,%s,%r)" % (
            self.index, self.term, self.kind, self.payload)


class Node:
    """模拟节点：只保存日志、提交位置与在线状态。"""

    def __init__(self, node_id):
        self.node_id = node_id
        self.online = True
        self.learner = False  # 新加入节点先作为无投票权的 learner 追赶日志
        self.log = []
        self.commit_index = 0
        self.term = 0

    @property
    def last_index(self):
        return len(self.log)

    @property
    def last_term(self):
        return self.log[-1].term if self.log else 0

    def has_entry(self, index, term):
        return len(self.log) >= index and self.log[index - 1].term == term

    def __repr__(self):
        flags = []
        if not self.online:
            flags.append("offline")
        if self.learner:
            flags.append("learner")
        suffix = (" " + ",".join(flags)) if flags else ""
        return "Node(%s log=%d commit=%d%s)" % (
            self.node_id, self.last_index, self.commit_index, suffix)
