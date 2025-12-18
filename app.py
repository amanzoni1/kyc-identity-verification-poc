import json
import base64
from io import BytesIO
from datetime import datetime
from typing import Optional, Dict, Any

import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field, field_validator, computed_field
from fireworks.client import Fireworks


# CONFIG
client = Fireworks()

MODEL = "accounts/fireworks/models/qwen3-vl-30b-a3b-instruct"

PROMPT = """
You are an expert identity document extractor.
Extract all visible identity fields from the image.
Return ONLY valid JSON using these keys:

{
  "document_type": "Driver's License" or "Passport" or "ID Card" or "Unknown" or null,
  "first_name": string or null, # Include middle names or initials in 'first_name' if present
  "last_name": string or null,
  "date_of_birth": "YYYY-MM-DD" or null,
  "gender": string or null,
  "nationality": string or null,
  "document_number": string or null,
  "issue_date": "YYYY-MM-DD" or null,
  "expiry_date": "YYYY-MM-DD" or null,
  "issuing_country": string or null,
  "address": string or null,
  "mrz_raw": string or null,  # If passport MRZ detected, extract the full raw MRZ text verbatim
  "confidence_score": float between 0.0 (low certainty) and 1.0 (high certainty) - based on overall image quality, text legibility, and how clearly fields are readable,
  "other_fields": {any additional relevant fields as key-value strings, or empty object}
}

Use null when unknown.
"""


# IMAGE PREPROCESSING
def resize_image(image_bytes: bytes, max_size: int = 1024) -> str:
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_size, max_size))
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# PYDANTIC MODEL
class KYCExtraction(BaseModel):
    document_type: Optional[str] = Field(None, description="Driver's License, Passport, etc.")
    first_name: str = Field(default=None, description="ALL given names/middle names exactly as they appear")
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None  # YYYY-MM-DD
    gender: Optional[str] = None
    nationality: Optional[str] = None
    document_number: Optional[str] = None
    issue_date: Optional[str] = None
    expiry_date: Optional[str] = None
    issuing_country: Optional[str] = None
    address: Optional[str] = None
    mrz_raw: Optional[str] = Field(None, description="Raw MRZ text if detected on passport")
    confidence_score: Optional[float] = Field(default=0.0, ge=0.0, le=1.0)
    other_fields: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("date_of_birth", "issue_date", "expiry_date", mode="before")
    @classmethod
    def normalize_dates(cls, v):
        if not v:
            return None
        formats = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y")
        for fmt in formats:
            try:
                return datetime.strptime(v.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None

    @computed_field
    @property
    def expiry_valid(self) -> bool:
        today = datetime.now()
        exp = self.expiry_date
        if exp is None:
            return False
        try:
            return datetime.strptime(exp, "%Y-%m-%d") > today
        except ValueError:
            return False

    @computed_field
    @property
    def full_name(self) -> Optional[str]:
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}".strip().title()
        return None


# POST-PROCESSING
def post_process(raw: Dict) -> Dict:
    try:
        extraction = KYCExtraction(**raw)
    except Exception as e:
        st.warning(f"Pydantic validation errors: {e}")
        safe_data = {k: v for k, v in raw.items() if k in KYCExtraction.model_fields}
        extraction = KYCExtraction(**safe_data)

    data = extraction.model_dump()

    # Text normalization
    def normalize_text(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        return " ".join(s.split()).strip().title()

    def normalize_address(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        cleaned = ", ".join(line.strip() for line in str(s).splitlines() if line.strip())
        return cleaned.title()

    doc_type = data.get("document_type")
    data["document_type"] = doc_type.strip() if isinstance(doc_type, str) else "Unknown"
    data["first_name"] = normalize_text(data.get("first_name"))
    data["last_name"] = normalize_text(data.get("last_name"))
    data["gender"] = data.get("gender").upper().strip() if data.get("gender") else None
    data["nationality"] = normalize_text(data.get("nationality"))
    data["issuing_country"] = data.get("issuing_country").upper().strip() if data.get("issuing_country") else None
    data["address"] = normalize_address(data.get("address"))
    if data.get("mrz_raw"):
        mrz = data["mrz_raw"]
        if isinstance(mrz, str):
            data["mrz_raw"] = mrz.replace("\r\n", " | ").replace("\n", " | ").replace("\r", " | ").strip()

    # Clean other_fields lightly
    for k, v in data.get("other_fields", {}).items():
        if isinstance(v, str):
            data["other_fields"][k] = " ".join(v.split()).strip()

    return data


# KYC VALIDATION
def validate_extraction(data: Dict) -> Dict:
    issues = []
    warnings = []

    # Critical: core identifiers
    if not data.get("document_number"):
        issues.append("Missing document number")
    if not data.get("first_name") and not data.get("last_name") and not data.get("full_name"):
        issues.append("Missing name information")
    if not data.get("date_of_birth"):
        issues.append("Missing date of birth")
    if not data.get("expiry_valid", False):
        issues.append("Document expired")

    # Warnings: anomalies / low quality
    if data.get("date_of_birth"):
        try:
            age = (datetime.now() - datetime.strptime(data["date_of_birth"], "%Y-%m-%d")).days // 365
            if age < 16 or age > 100:
                warnings.append(f"Unusual age ({age} years)")
        except (ValueError, TypeError) as e:
            warnings.append(f"Invalid date of birth format")

    if data.get("confidence_score", 1.0) < 0.7:
        warnings.append(f"Low model confidence ({data['confidence_score']:.2f}) – recommend manual review")

    validation = {
        "status": "APPROVED" if not issues else "REJECTED",
        "critical_issues": issues or None,
        "warnings": warnings or None,
    }

    return validation


# STREAMLIT UI
st.title("KYC Identity Verification PoC")

uploaded_files = st.file_uploader(
    "Upload document images",
    type=["jpg", "jpeg", "png"],
    accept_multiple_files=True,
    help="You can upload multiple documents at once (e.g., all 5 provided samples)"
)

if uploaded_files:
    st.write(f"**{len(uploaded_files)} document(s) uploaded**")

    if st.button("Analyze All Documents", type="primary"):
        results = []

        progress_bar = st.progress(0)
        status_text = st.empty()

        for idx, uploaded in enumerate(uploaded_files):
            status_text.text(f"Processing {uploaded.name}... ({idx+1}/{len(uploaded_files)})")
            try:
                bytes_data = uploaded.getvalue()
                b64 = resize_image(bytes_data)
                image_url = f"data:image/jpeg;base64,{b64}"

                response = client.chat.completions.create(
                    model=MODEL,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": PROMPT},
                                {"type": "image_url", "image_url": {"url": image_url}}
                            ]
                        }
                    ],
                    temperature=0.0,
                    top_p=0.95,
                    max_tokens=1024,
                    response_format={"type": "json_object"},
                    perf_metrics_in_response=True,
                )

                raw_content = response.choices[0].message.content.strip()
                raw_data = json.loads(raw_content)

                cleaned = post_process(raw_data)
                cleaned["kyc_validation"] = validate_extraction(cleaned)
                cleaned["filename"] = uploaded.name

                # Collect per-document performance metrics
                perf = {}
                if hasattr(response, "usage") and response.usage:
                    perf["prompt_tokens"] = response.usage.prompt_tokens
                    perf["completion_tokens"] = response.usage.completion_tokens or 'N/A'
                    perf["total_tokens"] = response.usage.total_tokens
                if hasattr(response, "perf_metrics") and response.perf_metrics:
                    ttft = response.perf_metrics.get("server-time-to-first-token")
                    processing = response.perf_metrics.get("server-processing-time")
                    perf["ttft"] = float(ttft) if ttft else None
                    perf["processing"] = float(processing) if processing else None
                cleaned["perf"] = perf

                results.append(cleaned)

            except Exception as e:
                st.error(f"Failed to process {uploaded.name}: {str(e)}")
                results.append({
                    "filename": uploaded.name,
                    "error": str(e),
                    "kyc_validation": {"status": "ERROR", "critical_issues": [], "warnings": []},
                    "perf": {"error": str(e)}
                })

            progress_bar.progress((idx + 1) / len(uploaded_files))

        status_text.empty()
        progress_bar.empty()
        st.success("Analysis Complete!")

        # Horizontal tab menu
        if results:
            tab_titles = []
            for res in results:
                name_no_ext = res["filename"].rsplit('.', 1)[0]
                short_name = name_no_ext[:25] + "..." if len(name_no_ext) > 25 else name_no_ext
                tab_titles.append(short_name)

            tabs = st.tabs(tab_titles)

            for tab, res in zip(tabs, results):
                with tab:
                    if "error" in res:
                        st.error(f"Processing error: {res['error']}")
                        continue

                    # Per-document Performance & Token Usage
                    with st.expander("Performance & Token Usage Details", expanded=False):
                        perf = res.get("perf", {})
                        if perf.get("error"):
                            st.write(f"Error: {perf['error']}")
                        else:
                            st.write("**Token Usage**")
                            st.write(f"- Prompt tokens: {perf.get('prompt_tokens', 'N/A')}")
                            st.write(f"- Completion tokens: {perf.get('completion_tokens', 'N/A')}")
                            st.write(f"- Total tokens: {perf.get('total_tokens', 'N/A')}")
                            st.write("**Latency Metrics**")
                            if perf.get("ttft"):
                                st.write(f"- Time to first token: {perf['ttft']:.3f}s")
                            if perf.get("processing"):
                                st.write(f"- Total server processing: {perf['processing']:.3f}s")

                    # Per-document Output & Validation
                    st.markdown("### Output")
                    display_data = {k: v for k, v in res.items() if k not in ["perf", "filename", "error"]}
                    st.json(display_data)

                    validation = res["kyc_validation"]
                    status = validation["status"]

                    if status == "APPROVED":
                        st.success("**KYC VERDICT: APPROVED**")
                    else:
                        st.error("**KYC VERDICT: REJECTED**")

                    if validation.get("critical_issues"):
                        st.markdown("#### Critical Issues (blocking approval)")
                        for issue in validation["critical_issues"]:
                            st.error(f"- {issue}")

                    if validation.get("warnings"):
                        st.markdown("#### Warnings (non-blocking)")
                        for warning in validation["warnings"]:
                            st.warning(f"- {warning}")

                    if not validation.get("critical_issues") and not validation.get("warnings"):
                        st.success("No issues or warnings detected.")

                    conf = res.get("confidence_score", 0.0)
                    if conf >= 0.9:
                        st.success(f"High model confidence: {conf:.2f}")
                    elif conf >= 0.7:
                        st.info(f"Moderate model confidence: {conf:.2f}")
                    else:
                        st.warning(f"Low model confidence: {conf:.2f}")

        # Batch Summary Report ONLY if more than one document
        if len(results) > 1:
            st.markdown("### Batch Summary Report")
            summary_data = []
            total_tokens_all = 0
            for res in results:
                perf = res.get("perf", {})
                total_tokens = perf.get("total_tokens", 0) if isinstance(perf.get("total_tokens"), (int, float)) else 0
                total_tokens_all += total_tokens
                summary_data.append({
                    "Filename": res["filename"],
                    "Document Type": res.get("document_type", "Unknown"),
                    "Full Name": res.get("full_name") or f"{res.get('first_name','')} {res.get('last_name','')}".strip(),
                    "Verdict": res["kyc_validation"]["status"],
                    "Confidence": f"{res.get('confidence_score', 0):.2f}",
                    "Total Tokens": perf.get("total_tokens", "N/A"),
                    "Issues/Warnings": len(res["kyc_validation"].get("critical_issues") or []) + len(res["kyc_validation"].get("warnings") or [])
                })
            st.table(summary_data)

            if total_tokens_all > 0:
                st.info(f"**Batch total tokens across all documents: {total_tokens_all}**")
