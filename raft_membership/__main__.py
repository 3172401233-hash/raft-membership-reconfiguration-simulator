"""演示：3 节点安全扩容到 5 节点的完整联合成员变更流程。

运行：python -m raft_membership
"""

from .simulator import Simulator


def main():
    sim = Simulator(["A", "B", "C"])
    sim.elect("A")
    sim.append("set x=1")
    sim.run_round()

    # 第一阶段：进入联合配置 (C_old, C_new)，D/E 以 learner 身份追赶
    sim.propose_config(["A", "B", "C", "D", "E"])
    sim.run_round()

    # 第二阶段：联合配置已提交，提交 C_new 退出联合
    sim.propose_config(["A", "B", "C", "D", "E"])
    sim.run_round()

    # 演示危险操作：缩容时节点离线导致 quorum 丢失
    sim.propose_config(["A", "B", "C"])
    sim.fail("D")
    sim.fail("E")
    sim.run_round()
    sim.recover("D")
    sim.run_round()

    print(sim.summary())


if __name__ == "__main__":
    main()
