from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Response
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
from models import Request, RequestStatus, DataType, PrivacyLevel
from schemas import RequestCreate, RequestResponse, RequestUpdate
from auth_utils import get_current_client, get_current_user
from datetime import datetime
from notification_service import notification_service
from data_masking import generate_synthetic_params, save_original_params, load_original_params
from plagiarism_checker import generate_plagiarism_report, save_report, load_report
import csv
import io
import json

router = APIRouter()

@router.post("/", response_model=RequestResponse)
@router.post("", response_model=RequestResponse)
async def create_request(
	request: RequestCreate,
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Create a new data generation request.

	- Persist original params to storage for future retrieval
	- Store synthetic params in DB to avoid keeping raw PII
	- Generate and save a plagiarism/similarity report between original and synthetic
	"""
	# Synthesize params for DB storage
	synthetic_params = generate_synthetic_params(request.params)

	db_request = Request(
		client_id=current_client.id,
		data_type=request.data_type,
		size=request.size,
		privacy_level=request.privacy_level,
		params=synthetic_params,
		status=RequestStatus.PENDING
	)
	
	db.add(db_request)
	db.commit()
	db.refresh(db_request)

	# Save original params to disk after we have an id
	save_original_params(db_request.id, request.params)

	# Generate and save plagiarism/similarity report
	try:
		original = load_original_params(db_request.id)
		report = generate_plagiarism_report(original, synthetic_params)
		save_report(db_request.id, report)
	except Exception:
		# Swallow report errors without failing request creation
		pass
	
	# Send submission notification to client
	if current_client.email:
		notification_service.send_request_submitted_notification(
			current_client.email,
			db_request.id,
			request.data_type.value
		)
	
	# Return with original params for UX (do not persist change)
	original = load_original_params(db_request.id)
	if original is not None:
		# Temporarily attach for response
		db_request.params = original
	
	return db_request


def _parse_params_content_to_json(content: Optional[str], params_format: Optional[str], filename: Optional[str]) -> Optional[str]:
	"""Convert uploaded file content (json or csv) to a params JSON string.
	CSV is converted to a dict of lists keyed by column names.
	"""
	if not content:
		return None
	fmt = (params_format or "").lower()
	is_json_ext = bool(filename and filename.lower().endswith(".json"))
	is_csv_ext = bool(filename and filename.lower().endswith(".csv"))
	if fmt == "json" or (not fmt and is_json_ext):
		try:
			obj = json.loads(content)
			return json.dumps(obj) if isinstance(obj, dict) else None
		except Exception:
			return None
	# Treat as CSV otherwise
	try:
		reader = csv.DictReader(io.StringIO(content))
		columns: dict[str, list] = {}
		for row in reader:
			for k, v in row.items():
				columns.setdefault(k, []).append(v)
		return json.dumps(columns) if columns else None
	except Exception:
		return None


def await_file_read_text(file: UploadFile) -> Optional[str]:
	"""Helper to read UploadFile fully as text (utf-8)."""
	try:
		data = file.file.read()
		if isinstance(data, bytes):
			return data.decode("utf-8", errors="ignore")
		return str(data)
	except Exception:
		return None

@router.options("/upload")
@router.options("/upload/")
async def options_upload() -> Response:
	return Response(status_code=200)

@router.post("/upload", response_model=RequestResponse)
@router.post("/upload/", response_model=RequestResponse)
async def create_request_upload(
	data_type: DataType = Form(...),
	size: int = Form(...),
	privacy_level: PrivacyLevel = Form(...),
	params_format: Optional[str] = Form(None),
	params_file: Optional[UploadFile] = File(None),
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Create request via multipart form with optional params file (JSON or CSV)."""
	params_json = None
	if params_file is not None:
		content = await_file_read_text(params_file)
		params_json = _parse_params_content_to_json(content, params_format, params_file.filename)

	# Synthesize params
	synthetic_params = generate_synthetic_params(params_json)

	db_request = Request(
		client_id=current_client.id,
		data_type=data_type,
		size=size,
		privacy_level=privacy_level,
		params=synthetic_params,
		status=RequestStatus.PENDING
	)
	
	db.add(db_request)
	db.commit()
	db.refresh(db_request)

	# Save original and report
	save_original_params(db_request.id, params_json)
	try:
		report = generate_plagiarism_report(params_json, synthetic_params)
		save_report(db_request.id, report)
	except Exception:
		pass

	# Notify
	if current_client.email:
		notification_service.send_request_submitted_notification(
			current_client.email,
			db_request.id,
			data_type.value
		)

	# Return with original
	original = load_original_params(db_request.id)
	if original is not None:
		db_request.params = original
	return db_request

@router.get("/", response_model=List[RequestResponse])
async def get_my_requests(
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Get all requests for the current client, showing original params if available."""
	requests = db.query(Request).filter(Request.client_id == current_client.id).all()
	for r in requests:
		original = load_original_params(r.id)
		if original is not None:
			# show original in API response only
			r.params = original
	return requests

@router.get("/{request_id}", response_model=RequestResponse)
async def get_request(
	request_id: int,
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Get a specific request by ID, showing original params if available."""
	request = db.query(Request).filter(
		Request.id == request_id,
		Request.client_id == current_client.id
	).first()
	
	if not request:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Request not found"
		)
	
	original = load_original_params(request.id)
	if original is not None:
		request.params = original
	
	return request

@router.get("/{request_id}/plagiarism-report")
async def get_plagiarism_report(
	request_id: int,
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Fetch the plagiarism/similarity report for a request."""
	request = db.query(Request).filter(
		Request.id == request_id,
		Request.client_id == current_client.id
	).first()
	if not request:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Request not found"
		)
	report = load_report(request_id)
	if report is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Report not found"
		)
	return report

@router.put("/{request_id}", response_model=RequestResponse)
async def update_request(
	request_id: int,
	request_update: RequestUpdate,
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Update a request (only if status is pending)."""
	request = db.query(Request).filter(
		Request.id == request_id,
		Request.client_id == current_client.id
	).first()
	
	if not request:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Request not found"
		)
	
	if request.status != RequestStatus.PENDING:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Cannot update request that is not pending"
		)
	
	# Update fields
	if request_update.status is not None:
		request.status = request_update.status
	if request_update.output_path is not None:
		request.output_path = request_update.output_path
	
	request.updated_at = datetime.utcnow()
	
	db.commit()
	db.refresh(request)
	
	return request

@router.delete("/{request_id}")
async def delete_request(
	request_id: int,
	current_client = Depends(get_current_client),
	db: Session = Depends(get_db)
):
	"""Delete a request (only if status is pending)."""
	request = db.query(Request).filter(
		Request.id == request_id,
		Request.client_id == current_client.id
	).first()
	
	if not request:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Request not found"
		)
	
	if request.status != RequestStatus.PENDING:
		raise HTTPException(
			status_code=status.HTTP_400_BAD_REQUEST,
			detail="Cannot delete request that is not pending"
		)
	
	db.delete(request)
	db.commit()
	
	return {"message": "Request deleted successfully"}
