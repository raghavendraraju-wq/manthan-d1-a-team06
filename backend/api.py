"""HTTP API. Every route checks the caller's role on the server (returnright.access)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from returnright.access import normalize_email
from returnright.contracts import Role, Screen

from .auth import Principal, issue_token, require_screen, staff_or_customer, verify_password
from .service import NotFound, Services

router = APIRouter(prefix="/api")

customer_only = require_screen(Screen.CUSTOMER_WIZARD)
agent_only = require_screen(Screen.SCAN_AND_GRADE)
finance_only = require_screen(Screen.APPROVAL_QUEUE)
manager_only = require_screen(Screen.DASHBOARD)


def svc(request: Request) -> Services:
    return request.app.state.services


def _own_order(principal: Principal, order_no: str) -> None:
    if principal.role is Role.CUSTOMER and principal.subject != order_no:
        raise HTTPException(status_code=404, detail="Not found.")


# ---------------------------------------------------------------- auth

class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class OtpRequestIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=200)


class OtpVerifyIn(OtpRequestIn):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/auth/login")
def login(body: LoginIn, request: Request, s: Services = Depends(svc)):
    user = s.staff_user(body.email)
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="We couldn't sign you in. Check your email and password.")
    principal = Principal(Role(user.role), user.email, user.name, user.email)
    settings = request.app.state.settings
    return {"token": issue_token(settings.jwt_secret, principal, settings.staff_token_minutes),
            "role": user.role, "name": user.name, "email": user.email}


@router.post("/auth/otp/request", status_code=202)
def otp_request(body: OtpRequestIn, s: Services = Depends(svc)):
    # Same answer whether or not the order matches, so the endpoint can't be used to probe orders.
    s.otp.issue(body.order_no.strip().upper(), body.email)
    return {"message": "If the order number and email match, we've sent a 6-digit code to that email."}


@router.post("/auth/otp/verify")
def otp_verify(body: OtpVerifyIn, request: Request, s: Services = Depends(svc)):
    order_no = body.order_no.strip().upper()
    if not s.otp.verify(order_no, body.email, body.code):
        raise HTTPException(status_code=401, detail="That code didn't match. Check the latest email and try again.")
    settings = request.app.state.settings
    principal = Principal(Role.CUSTOMER, order_no, "", normalize_email(body.email))
    return {"token": issue_token(settings.jwt_secret, principal, settings.customer_token_minutes),
            "role": "customer", "order_no": order_no}


@router.get("/auth/me")
def me(principal: Principal = Depends(staff_or_customer)):
    return {"role": principal.role.value, "subject": principal.subject, "name": principal.name}


# ---------------------------------------------------------------- customer

class ReturnItemIn(BaseModel):
    line_no: int = Field(ge=1)
    reason: str = Field(max_length=40)
    photo_id: str | None = Field(default=None, max_length=40)


class ReturnIn(BaseModel):
    order_no: str = Field(max_length=20)
    refund_method: Literal["original_payment", "store_credit"]
    items: list[ReturnItemIn] = Field(min_length=1, max_length=50)


@router.get("/orders/{order_no}")
def get_order(order_no: str, p: Principal = Depends(customer_only), s: Services = Depends(svc)):
    _own_order(p, order_no)
    return s.order_view(order_no)


@router.post("/photos", status_code=201)
async def upload_photo(file: UploadFile = File(...), p: Principal = Depends(customer_only),
                       s: Services = Depends(svc)):
    data = await file.read(s.max_upload_bytes + 1)
    return {"photo_id": s.save_photo(p.subject, file.content_type or "", data)}


@router.post("/returns", status_code=201)
def create_return(body: ReturnIn, p: Principal = Depends(customer_only), s: Services = Depends(svc)):
    _own_order(p, body.order_no)
    return s.create_return(body.order_no, body.refund_method, [i.model_dump() for i in body.items])


@router.get("/returns/{return_id}")
def get_return(return_id: str, p: Principal = Depends(staff_or_customer), s: Services = Depends(svc)):
    _own_order(p, s.return_order_no(return_id))
    return s.return_view(return_id)


@router.post("/returns/{return_id}/items/{item_id}/cancel")
def cancel_item(return_id: str, item_id: str, p: Principal = Depends(customer_only), s: Services = Depends(svc)):
    _own_order(p, s.return_order_no(return_id))
    return s.cancel_item(return_id, item_id)


@router.get("/photos/{photo_id}")
def get_photo(photo_id: str, p: Principal = Depends(staff_or_customer), s: Services = Depends(svc)):
    photo, path = s.photo(photo_id)
    _own_order(p, photo.order_no)
    if not path.exists():
        raise NotFound(photo_id)
    return FileResponse(path, media_type=photo.content_type)


# ---------------------------------------------------------------- warehouse agent

class ScanIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)


class GradeIn(BaseModel):
    item_id: str = Field(max_length=40)
    grade: Literal["A", "B", "C"]
    decision: Literal["accept", "reject"]


class InspectIn(BaseModel):
    key_seed: str = Field(min_length=8, max_length=80)
    items: list[GradeIn] = Field(min_length=1, max_length=50)


@router.get("/agent/queue")
def agent_queue(_: Principal = Depends(agent_only), s: Services = Depends(svc)):
    return s.agent_queue()


@router.post("/agent/scan")
def agent_scan(body: ScanIn, _: Principal = Depends(agent_only), s: Services = Depends(svc)):
    return s.agent_scan(body.code)


@router.post("/agent/returns/{return_id}/inspect")
def agent_inspect(return_id: str, body: InspectIn, _: Principal = Depends(agent_only), s: Services = Depends(svc)):
    return s.agent_inspect(return_id, body.key_seed, [i.model_dump() for i in body.items])


# ---------------------------------------------------------------- finance

class ApproveIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=200)


@router.get("/finance/queue")
def finance_queue(_: Principal = Depends(finance_only), s: Services = Depends(svc)):
    return s.finance_queue()


@router.post("/finance/items/{item_id}/approve")
def finance_approve(item_id: str, body: ApproveIn, idempotency_key: str | None = Header(default=None),
                    _: Principal = Depends(finance_only), s: Services = Depends(svc)):
    return s.finance_decide(item_id, True, idempotency_key or body.idempotency_key)


@router.post("/finance/items/{item_id}/reject")
def finance_reject(item_id: str, _: Principal = Depends(finance_only), s: Services = Depends(svc)):
    return s.finance_decide(item_id, False)


@router.post("/finance/items/{item_id}/retry")
def finance_retry(item_id: str, _: Principal = Depends(finance_only), s: Services = Depends(svc)):
    return s.finance_retry(item_id)


# ---------------------------------------------------------------- manager

@router.get("/manager/dashboard")
def dashboard(days: int = Query(default=30), _: Principal = Depends(manager_only), s: Services = Depends(svc)):
    if days not in (7, 30, 90):
        raise HTTPException(status_code=422, detail="Period must be 7, 30 or 90 days.")
    return s.dashboard(days)


@router.get("/manager/items/{item_id}/audit")
def audit(item_id: str, _: Principal = Depends(manager_only), s: Services = Depends(svc)):
    return {"item_id": item_id, "entries": s.audit_trail(item_id)}


@router.get("/manager/returns/{return_id}")
def manager_return(return_id: str, _: Principal = Depends(manager_only), s: Services = Depends(svc)):
    return s.return_view(return_id, with_label=False)


# ---------------------------------------------------------------- system + demo

def system_only(request: Request, x_system_token: str | None = Header(default=None)) -> None:
    if x_system_token != request.app.state.settings.system_token:
        raise HTTPException(status_code=403, detail="System token required.")


class CarrierIn(BaseModel):
    return_id: str = Field(max_length=20)


@router.post("/system/carrier/pickup", dependencies=[Depends(system_only)])
def carrier_pickup(body: CarrierIn, s: Services = Depends(svc)):
    return {"picked_up": s.carrier_pickup(body.return_id)}


@router.post("/system/reconcile", dependencies=[Depends(system_only)])
def reconcile(s: Services = Depends(svc)):
    return {"settled": s.run_reconcile_job()}


@router.get("/demo/info")
def demo_info(request: Request):
    settings = request.app.state.settings
    if not settings.demo_mode:
        return {"demo": False}
    return {"demo": True, "staff": [
        {"role": "agent", "email": "arjun@returnright.test"},
        {"role": "finance", "email": "meera@returnright.test"},
        {"role": "manager", "email": "vikram@returnright.test"}],
        "password": settings.demo_staff_password,
        "orders": [{"order_no": "RR-10482", "email": "priya.s@example.com"}],
        "mailbox": "http://localhost:8025"}


@router.get("/demo/last-code")
def demo_last_code(email: str, request: Request):
    """DEMO_MODE only: the last one-time code sent to an address (used by the Playwright tests)."""
    if not request.app.state.settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found.")
    last = request.app.state.mailer.last.get(normalize_email(email))
    if last is None:
        raise HTTPException(status_code=404, detail="No code sent to that address.")
    import re
    match = re.search(r"\b(\d{6})\b", last[1])
    return {"code": match.group(1) if match else None}


@router.get("/health")
def health():
    return {"ok": True}
