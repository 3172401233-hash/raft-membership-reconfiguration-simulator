# -*- coding: utf-8 -*-
import unittest

from raft_membership import LogEntry, MembershipConfig, Simulator


def config_entries(node):
    return [e for e in node.log if e.kind == LogEntry.CONFIG]


class TestSafeExpansion(unittest.TestCase):
    """安全扩容：3 节点扩到 5 节点，走完整联合流程。"""

    def test_expand_3_to_5(self):
        sim = Simulator(["A", "B", "C"])
        self.assertTrue(sim.elect("A"))
        sim.append("x=1")
        sim.run_round()

        # 进入联合配置；D/E 作为 learner 加入，暂无投票权
        self.assertTrue(sim.propose_config(["A", "B", "C", "D", "E"]))
        self.assertTrue(sim.nodes["D"].learner)
        self.assertTrue(sim.nodes["E"].learner)
        report = sim.run_round()
        joint = MembershipConfig({"A", "B", "C"}, {"A", "B", "C", "D", "E"})
        self.assertEqual(sim.committed_config, joint)
        self.assertTrue(sim.effective_config().is_joint)
        # 新节点已追平并转为投票成员
        self.assertFalse(sim.nodes["D"].learner)
        self.assertEqual(sim.nodes["D"].last_index, sim.nodes["A"].last_index)
        # 联合阶段两个多数集合都必须成立
        self.assertTrue(all(report.quorum.values()))
        self.assertEqual(len(report.quorum), 2)

        # 提交 C_new，退出联合
        self.assertTrue(sim.propose_config(["A", "B", "C", "D", "E"]))
        report = sim.run_round()
        final = MembershipConfig({"A", "B", "C", "D", "E"})
        self.assertEqual(sim.committed_config, final)
        self.assertFalse(sim.effective_config().is_joint)
        self.assertEqual(report.committed_config, final)
        # 配置日志连续：领导日志中能找到已提交配置条目
        committed_configs = [e.config for e in config_entries(sim.nodes["A"])
                             if e.index <= sim.nodes["A"].commit_index]
        self.assertIn(final, committed_configs)


class TestDangerousShrink(unittest.TestCase):
    """危险缩容：联合配置未提交时拒绝二次变更；离线导致 quorum 丢失。"""

    def test_shrink_reject_and_quorum_loss(self):
        sim = Simulator(["A", "B", "C", "D", "E"])
        sim.elect("A")
        sim.run_round()

        # 联合配置尚未提交时再次变更成员 -> 必须拒绝
        self.assertTrue(sim.propose_config(["A", "B", "C"]))
        self.assertFalse(sim.propose_config(["A", "B"]))
        report = sim.run_round()
        self.assertTrue(any("尚未提交" in r for r in report.rejections))
        # 联合配置已提交
        self.assertTrue(sim.committed_config.is_joint)

        # C/D/E 离线：旧集合 {A..E} 需要 3 票，只剩 A/B -> quorum 丢失
        for nid in ["C", "D", "E"]:
            sim.fail(nid)
        sim.append("blocked")
        report = sim.run_round()
        self.assertIn(False, report.quorum.values())
        # 联合阶段要求旧集合多数，条目无法提交
        self.assertTrue(any("暂不可提交" in e for e in report.events))
        self.assertEqual(sim.nodes["A"].commit_index, 1)

        # 恢复 C 后两个集合都恢复多数，阻塞条目提交
        sim.recover("C")
        report = sim.run_round()
        self.assertTrue(all(report.quorum.values()))
        self.assertEqual(sim.nodes["A"].commit_index, 2)

        # 提交 C_new，退出联合
        self.assertTrue(sim.propose_config(["A", "B", "C"]))
        sim.run_round()
        self.assertEqual(sim.committed_config,
                         MembershipConfig({"A", "B", "C"}))


class TestLeaderSwitchInJoint(unittest.TestCase):
    """联合配置中的领导切换与旧领导提交能力。"""

    def test_leader_switch_and_old_leader_commit(self):
        sim = Simulator(["A", "B", "C"])
        sim.elect("A")
        sim.propose_config(["A", "B", "C", "D", "E"])
        sim.run_round()
        self.assertTrue(sim.committed_config.is_joint)

        # 联合阶段旧领导 A 仍能提交普通条目
        sim.append("during-joint")
        sim.run_round()
        leader = sim.nodes["A"]
        self.assertEqual(leader.commit_index, leader.last_index)

        # 领导切换：A 离线，B 必须同时赢得新旧两个多数集合
        sim.fail("A")
        self.assertTrue(sim.elect("B"))
        report = sim.run_round()
        self.assertEqual(report.leader, "B")
        self.assertTrue(all(report.quorum.values()))

        # 新领导提交 C_new，退出联合
        self.assertTrue(sim.propose_config(["A", "B", "C", "D", "E"]))
        sim.run_round()
        self.assertEqual(sim.committed_config,
                         MembershipConfig({"A", "B", "C", "D", "E"}))

    def test_old_leader_steps_down_after_new_config(self):
        sim = Simulator(["A", "B", "C", "D", "E"])
        sim.elect("E")
        sim.run_round()
        # 缩容到 {A,B,C}：E 在旧配置中但不在新配置中
        sim.propose_config(["A", "B", "C"])
        sim.run_round()
        self.assertTrue(sim.committed_config.is_joint)
        # 联合阶段 E 仍是合法领导，可以提交
        sim.append("old-leader-entry")
        sim.run_round()
        self.assertEqual(sim.nodes["E"].commit_index,
                         sim.nodes["E"].last_index)
        # C_new 提交后，E 不在新配置中，必须退位
        sim.propose_config(["A", "B", "C"])
        report = sim.run_round()
        self.assertIsNone(sim.leader_id)
        self.assertTrue(any("退位" in e for e in report.events))
        self.assertEqual(sim.committed_config,
                         MembershipConfig({"A", "B", "C"}))


class TestNodeRecovery(unittest.TestCase):
    """节点离线后 quorum 变化与恢复后的追赶。"""

    def test_recovery_catchup(self):
        sim = Simulator(["A", "B", "C"])
        sim.elect("A")
        sim.append("e1")
        sim.run_round()

        # B、C 相继离线：quorum 丢失，提交停滞
        sim.fail("B")
        sim.fail("C")
        sim.append("e2")
        report = sim.run_round()
        self.assertIn(False, report.quorum.values())
        self.assertEqual(sim.nodes["A"].commit_index, 1)

        # B 恢复：追上日志，quorum 恢复，e2 提交
        sim.recover("B")
        report = sim.run_round()
        self.assertTrue(all(report.quorum.values()))
        self.assertEqual(sim.nodes["B"].last_index, 2)
        self.assertEqual(sim.nodes["A"].commit_index, 2)
        self.assertTrue(any("复制" in e and "B" in e for e in report.events))


class TestDuplicateLogs(unittest.TestCase):
    """重复日志与重复配置项。"""

    def test_idempotent_replication(self):
        sim = Simulator(["A", "B", "C"])
        sim.elect("A")
        sim.append("only-once")
        sim.run_round()
        sim.run_round()  # 重复复制不应产生重复条目
        for nid in ["A", "B", "C"]:
            self.assertEqual(len(sim.nodes[nid].log), 1)

    def test_duplicate_config_rejected(self):
        sim = Simulator(["A", "B", "C"])
        sim.elect("A")
        # 与当前生效配置相同 -> 重复配置项
        self.assertFalse(sim.propose_config(["A", "B", "C"]))
        report = sim.run_round()
        self.assertTrue(any("重复配置项" in r for r in report.rejections))

        # 提交联合配置后，重复提议同一目标 -> 拒绝
        self.assertTrue(sim.propose_config(["A", "B", "C", "D"]))
        sim.run_round()
        self.assertTrue(sim.propose_config(["A", "B", "C", "D"]))
        sim.run_round()
        self.assertFalse(sim.propose_config(["A", "B", "C", "D"]))
        report = sim.run_round()
        self.assertTrue(any("重复配置项" in r for r in report.rejections))
        # 日志中相同配置条目只有一份
        for nid in ["A", "B", "C"]:
            configs = [e.config for e in config_entries(sim.nodes[nid])]
            self.assertEqual(len(configs), len(set(configs)))


class TestConfigRollback(unittest.TestCase):
    """未提交的配置在领导切换后回退。"""

    def test_uncommitted_config_rolled_back(self):
        sim = Simulator(["A", "B", "C", "D", "E"])
        sim.elect("A")
        # A 在其他节点离线时提议联合配置，条目无法提交
        for nid in ["B", "C", "D", "E"]:
            sim.fail(nid)
        self.assertTrue(sim.propose_config(["A", "B", "C", "D", "E", "F"]))
        sim.run_round()
        self.assertFalse(sim.committed_config.is_joint)

        # 其余节点恢复，B 以更新任期当选（无需 A/F 的票）；
        # A 与 F 日志中的未提交配置被截断回滚
        for nid in ["B", "C", "D", "E"]:
            sim.recover(nid)
        self.assertTrue(sim.elect("B"))
        report = sim.run_round()
        self.assertTrue(any("回滚未提交配置" in e for e in report.events))
        self.assertEqual(config_entries(sim.nodes["A"]), [])
        self.assertEqual(config_entries(sim.nodes["F"]), [])
        # 生效配置回退到已提交的旧配置
        self.assertEqual(sim.effective_config(),
                         MembershipConfig({"A", "B", "C", "D", "E"}))


if __name__ == "__main__":
    unittest.main()
