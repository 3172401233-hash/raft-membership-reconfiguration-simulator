"""成员配置：简单配置 C 或联合配置 (C_old, C_new)。"""


class MembershipConfig:
    """Raft 成员配置。

    简单配置只包含一个多数集合；联合配置包含新旧两个多数集合，
    任何决议（选举、提交）都必须同时在两个集合中各自达成多数，
    从而避免变更期间出现两个互不相交的多数派。
    """

    __slots__ = ("old", "new")

    def __init__(self, old, new=None):
        old = frozenset(old)
        if not old:
            raise ValueError("成员配置不能为空")
        self.old = old
        self.new = frozenset(new) if new is not None else None

    @property
    def is_joint(self):
        return self.new is not None

    def voter_sets(self):
        """返回需要各自达成多数的投票集合列表。"""
        if self.is_joint:
            return [self.old, self.new]
        return [self.old]

    def members(self):
        if self.is_joint:
            return self.old | self.new
        return self.old

    def quorum_status(self, available):
        """检查 available 节点是否能在每个多数集合中构成多数。

        返回 (ok, reasons)，reasons 列出无法构成多数的集合。
        """
        ok = True
        reasons = []
        for voters in self.voter_sets():
            online = sorted(set(voters) & set(available))
            if len(online) <= len(voters) // 2:
                ok = False
                reasons.append(
                    "多数集合 %s 需要 >%d 票，仅 %s 可用"
                    % (_fmt(voters), len(voters) // 2, online)
                )
        return ok, reasons

    def has_quorum(self, available):
        return self.quorum_status(available)[0]

    def __eq__(self, other):
        return (
            isinstance(other, MembershipConfig)
            and self.old == other.old
            and self.new == other.new
        )

    def __hash__(self):
        return hash((self.old, self.new))

    def __repr__(self):
        if self.is_joint:
            return "%s+%s(joint)" % (_fmt(self.old), _fmt(self.new))
        return _fmt(self.old)


def _fmt(members):
    return "{" + ",".join(sorted(members)) + "}"
