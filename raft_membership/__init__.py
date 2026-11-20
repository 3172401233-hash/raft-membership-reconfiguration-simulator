"""Raft 联合成员变更（Joint Consensus）状态机模拟器。

注意：这是一个状态机模拟器，用于验证成员变更各阶段的多数集合、
配置日志连续性、旧领导提交能力和离线后的 quorum 是否成立。
它不是 Raft 数据库，不持久化数据，也不实现真实的网络 RPC。
"""

from .config import MembershipConfig
from .node import LogEntry, Node
from .simulator import Simulator, RoundReport

__all__ = ["MembershipConfig", "LogEntry", "Node", "Simulator", "RoundReport"]
