"""R-1 at this project's boundary: adapt the frozen runtime's boolean approver hook to the approval service WITHOUT changing the runtime.

The runtime's `Policy.approver(name, args) -> bool` carries no role, expiry or diff. The callback built here is bound to ONE approval record and ONE exact validated action; it
returns True only if the approval service says that record authorises exactly that action right now (role, tenant, case, expiry, canonical hash). The runtime's own boolean is
therefore never the authority: the record is. A callback is single-purpose by construction; handing the runtime a callback that always returns True is the thing this exists to avoid.
"""
from __future__ import annotations

from collections.abc import Callable

from .actions import ValidatedAction
from .approvals import ApprovalError, ApprovalService


def approver_for(service: ApprovalService, approval_id: str | None, action: ValidatedAction, account_id: str) -> Callable[[str, dict], bool]:
    def approver(name: str, args: dict) -> bool:
        if name != action.type or args != action.params:           # the runtime is about to call something other than what was approved
            return False
        try:
            service.check_for_execution(approval_id, action, account_id)
        except ApprovalError:
            return False
        return True
    return approver
