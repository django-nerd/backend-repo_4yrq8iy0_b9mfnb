import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Literal, Any, Dict
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import (
    User,
    WalletTransaction,
    Campaign,
    SellerAcceptance,
    RoutingAssignment,
    CallRecord,
    Notification,
)

app = FastAPI(title="Live Transfers Exchange API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------- Helpers ---------

def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = dict(doc)
    if d.get("_id"):
        d["id"] = str(d.pop("_id"))
    # Convert ObjectId inside known fields
    for k in ["buyer_id", "seller_id", "campaign_id", "call_id", "user_id"]:
        if k in d and isinstance(d[k], ObjectId):
            d[k] = str(d[k])
    return d


def get_balance(user_id: str) -> float:
    txs = db["wallettransaction"].find({"user_id": user_id})
    balance = 0.0
    for t in txs:
        if t.get("type") == "credit":
            balance += float(t.get("amount", 0))
        else:
            balance -= float(t.get("amount", 0))
    return round(balance, 2)


# -------- Root & Health ---------

@app.get("/")
def read_root():
    return {"message": "Live Transfers Exchange API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
            response["database"] = "✅ Connected & Working"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    return response


# -------- Users ---------

@app.post("/users")
def create_user(user: User):
    # Ensure unique email
    existing = db["user"].find_one({"email": user.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    user_id = create_document("user", user)
    # Starter notification
    create_document(
        "notification",
        Notification(user_id=user_id, message=f"Welcome to Live Transfers Exchange, {user.name}!")
        .model_dump(),
    )
    return {"id": user_id}


@app.get("/users")
def list_users(role: Optional[str] = None):
    q = {"role": role} if role else {}
    docs = db["user"].find(q).limit(50)
    return [serialize(d) for d in docs]


# -------- Wallet ---------

class TopUp(BaseModel):
    user_id: str
    amount: float


@app.post("/wallet/topup")
def wallet_topup(payload: TopUp):
    if payload.amount < 50:
        raise HTTPException(status_code=400, detail="Minimum top-up is $50")
    # Ensure user exists and is buyer
    user = db["user"].find_one({"_id": oid(payload.user_id)})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    tx = WalletTransaction(user_id=payload.user_id, type="credit", amount=payload.amount, memo="Account funding")
    tx_id = create_document("wallettransaction", tx)
    return {"id": tx_id, "balance": get_balance(payload.user_id)}


@app.get("/wallet/balance/{user_id}")
def wallet_balance(user_id: str):
    return {"user_id": user_id, "balance": get_balance(user_id)}


# -------- Campaigns ---------

@app.post("/campaigns")
def create_campaign(campaign: Campaign):
    # Validate buyer exists and role
    buyer = db["user"].find_one({"_id": oid(campaign.buyer_id)})
    if not buyer or buyer.get("role") != "buyer":
        raise HTTPException(status_code=400, detail="Invalid buyer")
    if campaign.price_per_call < 35:
        raise HTTPException(status_code=400, detail="Minimum price per call is $35")
    camp_id = create_document("campaign", campaign)
    # Notify sellers that a new campaign is available
    sellers = db["user"].find({"role": "seller"})
    for s in sellers:
        create_document(
            "notification",
            Notification(user_id=str(s["_id"]), message=f"New campaign available: {campaign.vertical}").model_dump(),
        )
    return {"id": camp_id}


@app.get("/campaigns")
def list_campaigns(role: Optional[str] = None, user_id: Optional[str] = None, status: Optional[str] = None):
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    if role == "buyer" and user_id:
        q["buyer_id"] = user_id
    docs = db["campaign"].find(q).sort("created_at", -1).limit(100)
    return [serialize(d) for d in docs]


@app.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str):
    doc = db["campaign"].find_one({"_id": oid(campaign_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Campaign not found")
    # Attach acceptances and routing
    accepts = list(db["selleracceptance"].find({"campaign_id": campaign_id}))
    routing = db["routingassignment"].find_one({"campaign_id": campaign_id})
    out = serialize(doc)
    out["acceptances"] = [serialize(a) for a in accepts]
    out["routing"] = serialize(routing) if routing else None
    return out


class AcceptPayload(BaseModel):
    seller_id: str
    status: Literal["accepted", "rejected"] = "accepted"


@app.post("/campaigns/{campaign_id}/accept")
def accept_campaign(campaign_id: str, payload: AcceptPayload):
    camp = db["campaign"].find_one({"_id": oid(campaign_id)})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    seller = db["user"].find_one({"_id": oid(payload.seller_id)})
    if not seller or seller.get("role") != "seller":
        raise HTTPException(status_code=400, detail="Invalid seller")
    # Upsert acceptance
    existing = db["selleracceptance"].find_one({"campaign_id": campaign_id, "seller_id": payload.seller_id})
    if existing:
        db["selleracceptance"].update_one({"_id": existing["_id"]}, {"$set": {"status": payload.status}})
    else:
        create_document("selleracceptance", SellerAcceptance(campaign_id=campaign_id, seller_id=payload.seller_id, status=payload.status))
    # Notify buyer
    create_document(
        "notification",
        Notification(user_id=camp["buyer_id"], message="A seller responded to your campaign.").model_dump(),
    )
    return {"ok": True}


class TransferNumberPayload(BaseModel):
    transfer_number: str


@app.post("/campaigns/{campaign_id}/transfer-number")
def set_transfer_number(campaign_id: str, payload: TransferNumberPayload):
    camp = db["campaign"].find_one({"_id": oid(campaign_id)})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db["campaign"].update_one({"_id": camp["_id"]}, {"$set": {"transfer_number": payload.transfer_number, "status": "awaiting_admin"}})
    # Notify admin placeholder
    create_document("notification", Notification(user_id="admin", message=f"Campaign {campaign_id} ready for routing").model_dump())
    return {"ok": True}


@app.post("/campaigns/{campaign_id}/assign-routing")
def assign_routing(campaign_id: str, routing: RoutingAssignment):
    camp = db["campaign"].find_one({"_id": oid(campaign_id)})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    # Upsert routing
    existing = db["routingassignment"].find_one({"campaign_id": campaign_id})
    if existing:
        db["routingassignment"].update_one({"_id": existing["_id"]}, {"$set": routing.model_dump()})
    else:
        create_document("routingassignment", routing)
    # Activate if buyer has >= $50, else set depleted
    buyer_id = camp["buyer_id"]
    bal = get_balance(buyer_id)
    new_status = "active" if bal >= 50 else "depleted"
    db["campaign"].update_one({"_id": camp["_id"]}, {"$set": {"status": new_status}})
    # Notify buyer & sellers
    create_document("notification", Notification(user_id=buyer_id, message="Your campaign routing is configured.").model_dump())
    for sid in routing.seller_ids:
        create_document("notification", Notification(user_id=sid, message="You have been assigned to a campaign.").model_dump())
    return {"ok": True, "status": new_status}


# -------- Calls & Billing ---------

class CallLogPayload(BaseModel):
    campaign_id: str
    seller_id: Optional[str] = None
    did_number: Optional[str] = None
    caller: Optional[str] = None
    called: Optional[str] = None
    duration_seconds: int
    recording_url: Optional[str] = None
    threshold: int = 90


@app.post("/calls/log")
def log_call(payload: CallLogPayload):
    camp = db["campaign"].find_one({"_id": oid(payload.campaign_id)})
    if not camp:
        raise HTTPException(status_code=404, detail="Campaign not found")
    buyer_id = camp["buyer_id"]
    billable = payload.duration_seconds >= max(60, payload.threshold)
    disposition = "completed" if payload.duration_seconds > 0 else "failed"
    record = CallRecord(
        campaign_id=payload.campaign_id,
        buyer_id=buyer_id,
        seller_id=payload.seller_id,
        did_number=payload.did_number,
        caller=payload.caller,
        called=payload.called,
        duration_seconds=payload.duration_seconds,
        billable_threshold=payload.threshold,
        billable=billable,
        recording_url=payload.recording_url,
        disposition="completed" if billable else ("short" if payload.duration_seconds > 0 else "failed"),
    )
    call_id = create_document("callrecord", record)

    # If billable, charge buyer
    if billable:
        price = float(camp.get("price_per_call", 0))
        tx = WalletTransaction(user_id=buyer_id, type="debit", amount=price, memo="Billable call", campaign_id=payload.campaign_id, call_id=call_id)
        create_document("wallettransaction", tx)
        # Pause/deplete if balance below 50
        bal = get_balance(buyer_id)
        if bal < 50:
            db["campaign"].update_one({"_id": camp["_id"]}, {"$set": {"status": "depleted"}})
            create_document("notification", Notification(user_id=buyer_id, message="Balance low: campaign paused. Please add funds.").model_dump())

    return {"id": call_id, "billable": billable}


@app.get("/calls")
def list_calls(campaign_id: Optional[str] = None, buyer_id: Optional[str] = None, seller_id: Optional[str] = None):
    q: Dict[str, Any] = {}
    if campaign_id:
        q["campaign_id"] = campaign_id
    if buyer_id:
        q["buyer_id"] = buyer_id
    if seller_id:
        q["seller_id"] = seller_id
    docs = db["callrecord"].find(q).sort("created_at", -1).limit(100)
    return [serialize(d) for d in docs]


# -------- Notifications ---------

@app.get("/notifications/{user_id}")
def notifications(user_id: str):
    docs = db["notification"].find({"user_id": user_id}).sort("created_at", -1).limit(50)
    return [serialize(d) for d in docs]


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
