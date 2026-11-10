# app/core/exceptions.py
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import HTTPException, RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

logger = logging.getLogger(__name__)

def setup_exception_handlers(app: FastAPI):
    """Configure les gestionnaires d'exceptions pour retourner du JSON"""
    
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """Gère toutes les exceptions HTTP (404, 500, etc.)"""
        logger.error(f"HTTP Exception {exc.status_code}: {exc.detail} - Path: {request.url.path}")
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": exc.detail,
                "status_code": exc.status_code,
                "path": str(request.url.path),
                "method": request.method
            }
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Gère les erreurs de validation"""
        logger.error(f"Validation error: {exc.errors()}")
        
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Erreur de validation",
                "validation_errors": exc.errors(),
                "path": str(request.url.path)
            }
        )
    
    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """Gère toutes les autres exceptions"""
        logger.error(f"Unexpected error: {str(exc)} - Path: {request.url.path}")
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Erreur interne du serveur",
                "error_type": type(exc).__name__,
                "path": str(request.url.path)
            }
        )