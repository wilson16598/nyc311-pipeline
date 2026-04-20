"""
Pydantic models for NYC 311 Service Request records.
Used for schema validation at ingestion and clean layer.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


VALID_BOROUGHS = {
    "BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNSPECIFIED"
}

VALID_STATUSES = {"Open", "Closed", "Pending", "Assigned", "In Progress"}


class RawRequest(BaseModel):
    """
    Schema for raw records as they arrive from the NYC Open Data API.
    Lenient — accepts nulls and messy strings so nothing is dropped at ingestion.
    """

    unique_key: str
    created_date: Optional[str] = None
    closed_date: Optional[str] = None
    agency: Optional[str] = None
    agency_name: Optional[str] = None
    complaint_type: Optional[str] = None
    descriptor: Optional[str] = None
    location_type: Optional[str] = None
    incident_zip: Optional[str] = None
    city: Optional[str] = None
    borough: Optional[str] = None
    status: Optional[str] = None
    resolution_description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    @field_validator("unique_key")
    @classmethod
    def key_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("unique_key cannot be empty")
        return v.strip()


class CleanRequest(BaseModel):
    """
    Schema for cleaned, validated records written to the clean layer.
    Stricter — enforces types, normalized values, and derived fields.
    """

    unique_key: str
    created_date: datetime
    closed_date: Optional[datetime] = None
    agency: str
    agency_name: str
    complaint_type: str
    descriptor: str
    location_type: str = "UNKNOWN"
    incident_zip: Optional[str] = None
    city: str = "UNKNOWN"
    borough: str = "UNSPECIFIED"
    status: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Derived fields
    resolution_hours: Optional[float] = Field(
        default=None,
        description="Hours between created and closed date",
    )
    is_resolved: bool = False
    complaint_category: str = Field(
        default="OTHER",
        description="Broad category derived from complaint_type",
    )
    year_month: str = Field(
        default="",
        description="YYYY-MM partition key derived from created_date",
    )

    @field_validator("borough")
    @classmethod
    def normalize_borough(cls, v: str) -> str:
        upper = v.upper().strip()
        return upper if upper in VALID_BOROUGHS else "UNSPECIFIED"

    @field_validator("incident_zip")
    @classmethod
    def validate_zip(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = v.strip()
        if re.fullmatch(r"\d{5}", cleaned):
            return cleaned
        return None

    @field_validator("latitude", "longitude")
    @classmethod
    def validate_coords(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        # NYC bounding box sanity check
        return v if -75.0 < v < -72.0 or 40.0 < v < 42.0 else None

    @model_validator(mode="after")
    def derive_fields(self) -> CleanRequest:
        # Resolution hours
        if self.closed_date and self.created_date:
            delta = (self.closed_date - self.created_date).total_seconds() / 3600
            self.resolution_hours = round(delta, 2) if delta >= 0 else None
            self.is_resolved = True

        # Year-month partition key
        self.year_month = self.created_date.strftime("%Y-%m")

        # Broad complaint category
        ct = self.complaint_type.upper()
        if any(kw in ct for kw in ["NOISE", "SOUND"]):
            self.complaint_category = "NOISE"
        elif any(kw in ct for kw in ["HEAT", "HOT WATER", "PLUMBING"]):
            self.complaint_category = "HOUSING"
        elif any(kw in ct for kw in ["STREET", "POTHOLE", "SIDEWALK", "HIGHWAY"]):
            self.complaint_category = "INFRASTRUCTURE"
        elif any(kw in ct for kw in ["SANIT", "TRASH", "LITTER", "GARBAGE"]):
            self.complaint_category = "SANITATION"
        elif any(kw in ct for kw in ["PARK", "TREE", "PLANT"]):
            self.complaint_category = "PARKS"
        elif any(kw in ct for kw in ["TAXI", "CAB", "VEHICLE", "PARKING"]):
            self.complaint_category = "TRANSPORTATION"
        else:
            self.complaint_category = "OTHER"

        return self
