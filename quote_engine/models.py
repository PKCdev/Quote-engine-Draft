from __future__ import annotations

from decimal import Decimal
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


Currency = Literal["AUD", "USD", "EUR", "GBP"]
Status = Literal["included", "excluded", "tbq"]


class Party(BaseModel):
    name: str
    title: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    abn: Optional[str] = None
    logo_path: Optional[str] = None


class ProjectInfo(BaseModel):
    name: str
    site_address: Optional[str] = None
    reference: Optional[str] = None
    date_issued: Optional[str] = None  # ISO string for simplicity


class MaterialLine(BaseModel):
    material: str
    thickness: Optional[str] = None
    sheet_size: Optional[str] = None
    qty: Decimal = Decimal(0)
    unit_cost: Decimal = Decimal(0)
    waste_percent: Optional[Decimal] = None  # if None, use policy default
    subtotal: Optional[Decimal] = None
    waste_cost: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None


class HardwareLine(BaseModel):
    name: str
    qty: Decimal = Decimal(0)
    unit_cost: Decimal = Decimal(0)
    total_cost: Optional[Decimal] = None


class BuyoutLine(BaseModel):
    material: str
    qty: Decimal = Decimal(0)
    unit: Optional[str] = None  # e.g., sq.m
    rate_per_unit: Optional[Decimal] = None
    total_cost: Optional[Decimal] = None
    status: Status = "included"


class LaborLine(BaseModel):
    category: str  # e.g., cad, cnc, handling, assembly, install
    hours: Decimal = Decimal(0)
    rate: Decimal = Decimal(0)
    total_cost: Optional[Decimal] = None
    description: Optional[str] = None


class ProductItem(BaseModel):
    index: str
    name: str
    dimensions: Optional[str] = None


class Room(BaseModel):
    code: str
    name: str
    products: List[ProductItem] = Field(default_factory=list)


class PaymentMilestone(BaseModel):
    name: str
    percent: Decimal
    description: Optional[str] = None
    base: Literal["subtotal", "grand_total"] = "grand_total"


class QuoteDisplay(BaseModel):
    show_excluded: bool = True
    show_dimensions: bool = True


class QuoteStyle(BaseModel):
    accent: str = "#3498db"
    header: str = "#2c3e50"
    body_font_px: int = 12
    table_font_px: int = 10


class PolicyConfig(BaseModel):
    currency: Currency = "AUD"
    currency_symbol: str = "$"
    gst_percent: Decimal = Decimal(10)
    waste_percent_default: Decimal = Decimal(8)
    payment_schedule: List[PaymentMilestone] = Field(default_factory=list)
    validity_days: int = 30
    display: QuoteDisplay = Field(default_factory=QuoteDisplay)
    style: QuoteStyle = Field(default_factory=QuoteStyle)


class RatesConfig(BaseModel):
    labor_rates: dict[str, Decimal] = Field(default_factory=dict)


class Adjustments(BaseModel):
    design_fee_percent: Optional[Decimal] = None
    delivery: Optional[Decimal] = None
    contingency_percent: Optional[Decimal] = None
    discount_percent: Optional[Decimal] = None


class QuoteData(BaseModel):
    client: Party
    company: Party
    project: ProjectInfo

    materials: List[MaterialLine] = Field(default_factory=list)
    hardware: List[HardwareLine] = Field(default_factory=list)
    buyout: List[BuyoutLine] = Field(default_factory=list)
    labor: List[LaborLine] = Field(default_factory=list)
    rooms: List[Room] = Field(default_factory=list)

    notes_inclusions: List[str] = Field(default_factory=list)
    notes_exclusions: List[str] = Field(default_factory=list)
    optional_upgrades: List[dict] = Field(default_factory=list)  # {name, price}

    adjustments: Adjustments = Field(default_factory=Adjustments)

