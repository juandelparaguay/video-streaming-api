from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import httpx
import uvicorn
import os
import json
import aiofiles

app = FastAPI()


@app.get("/")
async def root():
    """ Responde con un mensaje de bienvenida
    
    Returns: 
        dict: Un diccionario con el mensaje de bienvenida
    Notes: 
        Este es el mensaje de bienvenida de la API.
    """
    return {"message": "Hello World"}


@app.get("/files")
async def get_files():
    
    RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
    
    # full_path = os.path.abspath('files')  Obtiene la ruta absoluta para seguridad

    if not os.path.exists(RUTA_VIDEOS):
        raise HTTPException(status_code=404, detail=f"La ruta no existe.")

    if not os.path.isdir(RUTA_VIDEOS):
        raise HTTPException(status_code=400, detail=f"La ruta no es un directorio.")

    try:
        contents = os.listdir(RUTA_VIDEOS)
        file_list = []
        for item in contents:
            item_path = os.path.join(RUTA_VIDEOS, item)
            file_info = {
                "name": item,
                "is_directory": os.path.isdir(item_path),
                "size": os.path.getsize(item_path) if not os.path.isdir(item_path) else None,
                "modified": os.path.getmtime(item_path)
            }
            file_list.append(file_info)

        return {"contents": file_list}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al explorar la carpeta: {str(e)}")
    
@app.get("/files/{filename}")
async def get_file(filename: str):
    
    RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
    
    file_path = os.path.join(RUTA_VIDEOS, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=400, detail="Path is not a file")
    async def video_stream():
        async with aiofiles.open(file_path, mode="rb") as file:
            while chunk := await file.read(1024 * 1024):  # Lee en chunks de 1MB
                yield chunk

    return StreamingResponse(video_stream(), media_type="video/mp4")

    
                
