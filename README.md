# KYC Identity Verification PoC using Fireworks AI

## Overview

End-to-end proof-of-concept for extracting structured identity information from passports and driver's licenses as part of a KYC process in Financial Services.

This solution uses Fireworks AI's vision-language model to directly process document images into structured JSON, followed by post-processing, normalization, and rule-based validation to produce a KYC verdict.

## Quick Start

### Prerequisites
- Python 3.10+
- Fireworks AI account and API key (create at https://fireworks.ai/)

### Local Installation
```
git clone <your-repo>
cd kyc-poc

python -m venv venv
source venv/bin/activate    # On macOS/Linux
#venv\Scripts\activate      # On Windows

# Install dependencies
pip install -r requirements.txt

# Set API key
export FIREWORKS_API_KEY="your_key_here"
```

### Run the App
```
streamlit run app.py
```

Open http://localhost:8501 in your browser.

### Alternative: Run with Docker (Recommended for reproducibility)
```
docker build -t kyc-poc .

docker run -p 8501:8501 -e FIREWORKS_API_KEY="your_key_here" kyc-poc
```
Open http://localhost:8501 in your browser.

## Usage
1. Upload one or more document images (.jpg, .jpeg, .png).
2. Click "Analyze Document(s)".
3. View extracted JSON, validation verdict, and token/latency metrics.

### Testing on Provided Samples

The assignment includes 5 sample documents here:
https://drive.google.com/drive/u/0/folders/1GNyJZ8bluOg_TBuYFfrSsE4WfRsLYEcN


## Design Decisions & Trade-offs

The assignment explicitly asked for documentation of design choices and their trade-offs. Below is a detailed breakdown of the most important decisions.

1. **End-to-End Vision-Language Model Extraction**
   **Choice**: Direct image-to-structured-JSON extraction using a Fireworks vision-language model.
   **Rationale**: The task requires using Fireworks AI's platform, which excels at multimodal models capable of understanding document layouts, abbreviations, and semantics in a single API call. This simplifies the pipeline and handles layout variations robustly.
   **Alternatives Considered**: Traditional OCR + regex/NER or template-based approaches – rejected due to the Fireworks mandate and higher maintenance for diverse documents.
   **Trade-offs**: Higher cost/latency than pure OCR, with minor hallucination risk (mitigated by deterministic settings and post-processing). Overall, superior accuracy and simplicity for this use case.

2. **Model Selection**
   **Choice**: `accounts/fireworks/models/qwen3-vl-30b-a3b-instruct` (30B parameters, Instruct variant).
   **Rationale**: For a quick PoC, I prioritized serverless deployment (simple, no management overhead). Among available serverless vision models on Fireworks, the 30B Instruct variant provided the best balance of cost, speed, and document understanding, with excellent results on the samples. The Instruct tuning is ideal for structured extraction tasks.
   **Alternatives Considered**: Larger MoE variants (higher accuracy but increased cost); Thinking variants (better for reasoning but unnecessary here). Dedicated (non-serverless) deployments could enable smaller models (e.g., Llama-3.2-11B-Vision) for even lower cost, but serverless was preferred for PoC simplicity.
   **Trade-offs**: May miss very fine handwritten details; production could add confidence-based routing to larger models.

3. **Structured Output: json_object vs json_schema**
   **Choice**: `response_format={"type": "json_object"}` with a detailed prompt-defined schema (instead of strict `json_schema`).
   **Rationale**: Document extraction involves ambiguity (abbreviations, partial text, layout variations). Strict `json_schema` (Fireworks' recommended default) enforces guarantees but reduces recall—the model conservatively nulls uncertain fields. `json_object` prioritizes higher recall, outputting best-guess values that downstream deterministic post-processing (Pydantic + normalization) can reliably clean and validate. Empirical testing showed better field completeness this way. This high-recall → strict-cleanup pattern is standard for vision-based document tasks.
   **Trade-offs**:

   | Mode            | Pros                          | Cons                              |
   |-----------------|-------------------------------|-----------------------------------|
   | json_schema     | Hard guarantees, no hallucinated fields | Lower recall on ambiguous/partial text |
   | json_object     | Higher recall, flexible for real-world variations | Requires robust post-processing (which we have via Pydantic) |

   The Pydantic layer complements (rather than duplicates) this choice—it handles normalization and validation that `json_schema` can't fully cover (e.g., date parsing, field synthesis). `json_schema` suits clean text domains; `json_object` is better here.

4. **Image Preprocessing**
   **Choice**: Resize to ≤1024px + JPEG compression (quality=85).
   **Rationale**: Standard practice for vision models to control token usage/latency while preserving legibility. Even though the provided samples are lower resolution, capping at 1024px ensures consistency for real-world higher-res uploads (e.g., phone photos) and keeps prompt tokens ~800–1000.
   **Trade-offs**: Minor quality loss on ultra-high-res images (negligible for document text extraction).

5. **Post-Processing & Validation**
   **Choice**: Pydantic model + custom normalization + rule-based KYC checks.
   **Rationale**: Ensures standardized, compliant output and explainable verdicts required in FSI. Model confidence is used only as a heuristic.
   **Trade-offs**: Rules require maintenance for regulatory changes; prioritizes auditability over pure ML flexibility.

6. **API Configuration**
   **Choice**: temperature=0.0, top_p=0.95, max_tokens=1024, perf_metrics enabled.
   **Rationale**: Temperature=0.0 for deterministic, reproducible extractions (critical for KYC). top_p=0.95 as a safe default (has minimal effect with temp=0 but allows slight diversity if needed in variants). Metrics for cost/latency monitoring.
   **Trade-offs**: Low temperature limits exploration but ensures consistency.

7. **UI Choice: Streamlit**
   **Choice**: Interactive demo (single or batch upload).
   **Rationale**: Enables rapid, hands-on evaluation by technical and non-technical reviewers.
   **Trade-offs**: Not production-ready (no auth/scaling); perfect for PoC.

## Performance on Provided Samples

- **Extraction Accuracy**: All 5 documents successfully extracted core fields (name, DOB, document number, expiry, etc.).
- **Average Latency**: ~3–4 seconds end-to-end.
- **Token Usage**: ~900 prompt + ~250 completion tokens per document.
- **Estimated Cost**: ~$0.0002–0.0003 per document (based on current Fireworks pricing).


Thank you for the opportunity!
