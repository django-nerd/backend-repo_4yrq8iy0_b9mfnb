"""
Database Schemas for Live Transfers Exchange

Each Pydantic model corresponds to a MongoDB collection (lowercased class name).
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field


# Users
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    role: Literal["buyer", "seller", "admin"] = Field(..., description="User role")
    company: Optional[str] = Field(None, description="Company name")
    phone: Optional[str] = Field(None, description="Contact phone")
    is_active: bool = Field(True)


# Wallet transactions (ledger)
class WalletTransaction(BaseModel):
    user_id: str = Field(..., description="User identifier (buyer)")
    type: Literal["credit", "debit"] = Field(...)
    amount: float = Field(..., gt=0)
    memo: Optional[str] = None
    campaign_id: Optional[str] = None
    call_id: Optional[str] = None


# Campaigns created by buyers
class Campaign(BaseModel):
    buyer_id: str
    vertical: Literal[
        "Mortgage",
        "Medicare",
        "ACA Health Insurance",
        "Final Expense Insurance",
        "Debt",
        "Solar",
        "Business Loans",
        "Home Services",
    ]
    price_per_call: float = Field(..., ge=35, description="Bid per qualified call, min $35")
    daily_cap: int = Field(..., ge=1, description="Calls per day")
    states: List[str] = Field(..., description="US states (2-letter)")
    time_start: str = Field(..., description="HH:MM 24h start time in buyer timezone")
    time_end: str = Field(..., description="HH:MM 24h end time in buyer timezone")
    transfer_number: Optional[str] = Field(None, description="Destination number for live transfers")
    status: Literal[
        "draft",
        "pending_acceptance",
        "awaiting_admin",
        "active",
        "paused",
        "depleted",
        "archived",
    ] = Field("pending_acceptance")


# Seller accepts a campaign
class SellerAcceptance(BaseModel):
    campaign_id: str
    seller_id: str
    status: Literal["accepted", "rejected"] = "accepted"


# Admin assignment of routing/DIDs
class RoutingAssignment(BaseModel):
    campaign_id: str
    seller_ids: List[str] = Field(..., description="Selected sellers for this campaign")
    did_number: str = Field(..., description="Purchased DID from Twilio used as ingress")


# Call records
class CallRecord(BaseModel):
    campaign_id: str
    buyer_id: str
    seller_id: Optional[str] = None
    did_number: Optional[str] = None
    caller: Optional[str] = None
    called: Optional[str] = None
    duration_seconds: int = 0
    billable_threshold: int = Field(90, description="Seconds threshold for billable")
    billable: bool = False
    recording_url: Optional[str] = None
    disposition: Literal[
        "completed",
        "no_answer",
        "busy",
        "failed",
        "short",
    ] = "completed"


# Simple notifications
class Notification(BaseModel):
    user_id: str
    message: str
    read: bool = False
