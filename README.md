# raft-membership-reconfiguration-simulator

模拟 Raft 联合成员变更（Joint Consensus）、日志复制和领导切换。

> **注意：这是一个状态机模拟器，不是 Raft 数据库。**
> 它不持久化数据、不实现真实 RPC，只按轮推进节点状态机，
> 用于验证成员变更各阶段的安全性条件。

## 背景

单节点替换和联合成员变更的风险不同。如果领导在联合配置尚未提交时
再次变更成员，新旧两个多数集合可能各自认为自己是合法的，从而破坏
安全性。本模拟器复现完整流程：

```
C_old  ->  (C_old, C_new) 联合配置  ->  C_new
```

## 功能

- 输入节点、任期、日志索引、当前成员、联合配置、复制位置、提交索引、
  领导选举与故障（离线/恢复）事件
- 每轮验证：
  - 每个阶段的多数集合（联合配置要求两个集合各自达成多数）
  - 配置日志是否连续（已提交配置必须存在于领导日志中）
  - 旧领导在联合阶段是否仍能提交、退出联合后是否应退位
  - 节点离线后 quorum 是否仍然成立
- 边界处理：新节点以 learner 身份追赶、领导切换、重复配置项拒绝、
  未提交配置在领导切换后回退
- 每轮输出：领导、quorum 状态、已提交成员配置、事件与拒绝原因

## 运行

需要 Python 3.8.2+。

```bash
python -m raft_membership   # 演示：3 节点扩容到 5 节点再缩容
python -m unittest          # 运行全部测试
```

## 测试覆盖

- `TestSafeExpansion`：安全扩容（3 -> 5），learner 追赶与转正
- `TestDangerousShrink`：危险缩容，联合配置未提交时拒绝二次变更、
  节点离线导致 quorum 丢失、恢复后继续提交
- `TestLeaderSwitchInJoint`：联合配置中的领导切换、旧领导提交能力
  与退位
- `TestNodeRecovery`：节点恢复后追赶日志、quorum 恢复
- `TestDuplicateLogs`：幂等复制不产生重复日志、重复配置项被拒绝
- `TestConfigRollback`：未提交的配置在领导切换后回退

## 代码结构

- `raft_membership/config.py`：成员配置（简单/联合）与多数集合判定
- `raft_membership/node.py`：日志条目与节点状态
- `raft_membership/simulator.py`：选举、复制、提交、配置变更与轮次报告
- `tests/test_membership.py`：单元测试
