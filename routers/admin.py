from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from database import get_db
from models import Request, RequestStatus
from schemas import RequestResponse, RequestUpdate
from auth_utils import get_current_admin
from datetime import datetime
from synthetic_generator import SyntheticDataGenerator
from storage_manager import StorageManager as SM
from notification_service import notification_service

router = APIRouter()

@router.get("/requests", response_model=List[RequestResponse])
async def get_all_requests(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all requests for admin review."""
    requests = db.query(Request).all()
    return requests

@router.get("/requests/pending", response_model=List[RequestResponse])
async def get_pending_requests(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all pending requests."""
    requests = db.query(Request).filter(Request.status == RequestStatus.PENDING).all()
    return requests

@router.post("/requests/{request_id}/process")
async def process_request(
    request_id: int,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Process a request by generating synthetic data (allowed when PENDING or FAILED)."""
    request = db.query(Request).filter(Request.id == request_id).first()
    
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )
    
    if request.status not in (RequestStatus.PENDING, RequestStatus.FAILED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request is not in a processable state"
        )
    
    try:
        # Update status to processing
        request.status = RequestStatus.PROCESSING
        request.updated_at = datetime.utcnow()
        db.commit()
        
        # Generate synthetic data
        generator = SyntheticDataGenerator()
        storage_manager = SM()
        
        # Generate the dataset
        dataset_path = generator.generate_dataset(
            data_type=request.data_type,
            size=request.size,
            privacy_level=request.privacy_level,
            params=request.params,
            request_id=request_id
        )
        
        # Store the dataset
        output_path = storage_manager.store_dataset(dataset_path, request_id)
        
        # Update request with output path and completed status
        request.status = RequestStatus.COMPLETED
        request.output_path = output_path
        request.processed_at = datetime.utcnow()
        request.updated_at = datetime.utcnow()
        
        db.commit()
        
        # Send completion notification to client
        if request.client and request.client.email:
            download_url = storage_manager.get_download_url(output_path)
            notification_service.send_request_completed_notification(
                request.client.email, 
                request_id, 
                download_url
            )
        
        return {
            "message": "Request processed successfully",
            "output_path": output_path,
            "status": "completed"
        }
        
    except Exception as e:
        # Mark request as failed
        request.status = RequestStatus.FAILED
        request.updated_at = datetime.utcnow()
        db.commit()
        
        # Send failure notification to client
        if request.client and request.client.email:
            notification_service.send_request_failed_notification(
                request.client.email, 
                request_id, 
                str(e)
            )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process request: {str(e)}"
        )

@router.post("/requests/process-all")
async def process_all_requests(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Process all PENDING and FAILED requests."""
    to_process = db.query(Request).filter(Request.status.in_([RequestStatus.PENDING, RequestStatus.FAILED])).all()
    results = []
    for r in to_process:
        try:
            # Transition to processing per-request
            r.status = RequestStatus.PROCESSING
            r.updated_at = datetime.utcnow()
            db.commit()
            
            generator = SyntheticDataGenerator()
            storage_manager = SM()
            dataset_path = generator.generate_dataset(
                data_type=r.data_type,
                size=r.size,
                privacy_level=r.privacy_level,
                params=r.params,
                request_id=r.id
            )
            output_path = storage_manager.store_dataset(dataset_path, r.id)
            r.status = RequestStatus.COMPLETED
            r.output_path = output_path
            r.processed_at = datetime.utcnow()
            r.updated_at = datetime.utcnow()
            db.commit()
            if r.client and r.client.email:
                download_url = storage_manager.get_download_url(output_path)
                notification_service.send_request_completed_notification(
                    r.client.email, r.id, download_url
                )
            results.append({"id": r.id, "status": "completed"})
        except Exception as e:
            r.status = RequestStatus.FAILED
            r.updated_at = datetime.utcnow()
            db.commit()
            if r.client and r.client.email:
                notification_service.send_request_failed_notification(
                    r.client.email, r.id, str(e)
                )
            results.append({"id": r.id, "status": "failed", "error": str(e)})
    return {"processed": results}

@router.put("/requests/{request_id}", response_model=RequestResponse)
async def update_request_status(
    request_id: int,
    request_update: RequestUpdate,
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Update request status (admin only)."""
    request = db.query(Request).filter(Request.id == request_id).first()
    
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
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

@router.get("/stats")
async def get_admin_stats(
    current_admin = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """Get admin dashboard statistics."""
    total_requests = db.query(Request).count()
    pending_requests = db.query(Request).filter(Request.status == RequestStatus.PENDING).count()
    processing_requests = db.query(Request).filter(Request.status == RequestStatus.PROCESSING).count()
    completed_requests = db.query(Request).filter(Request.status == RequestStatus.COMPLETED).count()
    failed_requests = db.query(Request).filter(Request.status == RequestStatus.FAILED).count()
    
    return {
        "total_requests": total_requests,
        "pending_requests": pending_requests,
        "processing_requests": processing_requests,
        "completed_requests": completed_requests,
        "failed_requests": failed_requests
    }
