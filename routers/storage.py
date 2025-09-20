from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import Request, RequestStatus
from schemas import DownloadResponse
from storage_manager import StorageManager
from datetime import datetime, timedelta
import os

router = APIRouter()

@router.get("/download/{request_id}", response_model=DownloadResponse)
async def get_download_link(
    request_id: int,
    db: Session = Depends(get_db)
):
    """Get download link for a completed request. No auth required."""
    request = db.query(Request).filter(Request.id == request_id).first()
    
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )
    
    if request.status != RequestStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request is not completed yet"
        )
    
    if not request.output_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output file not found"
        )
    
    storage_manager = StorageManager()
    download_url = storage_manager.get_download_url(request.output_path)
    
    expires_at = datetime.utcnow() + timedelta(hours=1)
    
    return DownloadResponse(
        download_url=download_url,
        expires_at=expires_at
    )

@router.get("/download/{request_id}/file")
async def download_file(
    request_id: int,
    db: Session = Depends(get_db)
):
    """Direct file download for completed requests. No auth required."""
    request = db.query(Request).filter(Request.id == request_id).first()
    
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found"
        )
    
    if request.status != RequestStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request is not completed yet"
        )
    
    if not request.output_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Output file not found"
        )
    
    if os.path.exists(request.output_path):
        filename = os.path.basename(request.output_path) or f"synthetic_data_{request_id}.csv"
        return FileResponse(
            path=request.output_path,
            filename=filename,
            media_type="text/csv"
        )
    else:
        storage_manager = StorageManager()
        download_url = storage_manager.get_download_url(request.output_path)
        return RedirectResponse(url=download_url)
