# from fastapi import FastAPI, HTTPException, Header, Request, Query
from fastapi import FastAPI, HTTPException, Query, Request, Header, Form
from fastapi.responses import StreamingResponse
from collections import Counter
import os
import json
import aiofiles
from dotenv import load_dotenv
from datetime import datetime
import subprocess
import httpx
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List

load_dotenv()

app = FastAPI()

STATUS_FILE = "stream_status.json"

# --- Funciones auxiliares para formato de datos ---
def save_status(data: Dict):
    """Guarda el estado de los streams activos en un archivo JSON."""
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_status() -> Dict:
    """Carga el estado de los streams activos."""
    if not os.path.exists(STATUS_FILE):
        return {}
    with open(STATUS_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return {}


def format_size(size_in_bytes: int):
    if size_in_bytes is None:
        return "N/A"
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"

def format_modified_time(timestamp: float):
    if timestamp is None:
        return "N/A"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

async def get_raw_duration(file_path: str) -> float:
    try:
        command = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except:
        return 0.0

async def get_video_duration_formatted(file_path: str) -> str:
    duration_seconds = await get_raw_duration(file_path)
    if duration_seconds <= 0.0: return "N/A"
    hours = int(duration_seconds // 3600)
    minutes = int((duration_seconds % 3600) // 60)
    seconds = int(duration_seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours > 0 else f"{minutes:02d}:{seconds:02d}"

# --- ENDPOINTS ---

@app.get("/")
async def root():
    return {"message": "Servidor de Video Streaming Activo"}

@app.post("/stream/start")
async def stream_start(name: str = Form(...), addr: str = Form(...)):
    """Llamado por Nginx on_publish."""
    status = load_status()
    status[name] = {
        "is_live": True,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "client_ip": addr
    }
    save_status(status)
    return {"status": "ok"}

@app.post("/stream/stop")
async def stream_stop(name: str = Form(...)):
    """Llamado por Nginx on_done."""
    status = load_status()
    if name in status:
        del status[name]
        save_status(status)
    return {"status": "ok"}

@app.get("/streams/active")
async def get_active_streams() -> List[Dict]:
    """
    Retorna la lista de transmisiones actuales formateada para Flutter.
    Ejemplo: [{"name": "norte", "is_live": true, ...}, {"name": "sur", ...}]
    """
    status_dict = load_status()
    active_list = []
    for name, info in status_dict.items():
        stream_info = {"name": name}
        stream_info.update(info)
        active_list.append(stream_info)
    return active_list

@app.get("/streams/active/{name}")
async def get_active_stream_by_name(name: str):
    """
    Busca una transmisión específica por nombre.
    Retorna el objeto con los datos o {is_live: false} si no existe.
    """
    status_dict = load_status()
    
    if name in status_dict:
        # Si existe, retornamos los datos incluyendo el nombre
        result = {"name": name}
        result.update(status_dict[name])
        return result
    
    # Si no existe, retornamos el objeto por defecto solicitado
    return {
        "name": name,
        "is_live": False,
        "message": "No hay una transmisión activa con ese nombre."
    }

@app.get("/stream-status")
async def get_stream_status():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8003/stat", timeout=2.0)
            
        if response.status_code != 200:
            return {"is_live": False, "message": "No se pudo conectar con Nginx stat"}

        root = ET.fromstring(response.content)
        streams_activos = []
        for app in root.findall(".//app"):
            app_name = app.find("name").text
            if app_name == "live":
                for stream in app.findall(".//stream"):
                    name = stream.find("name").text
                    publisher = stream.find("publisher")
                    if publisher is not None:
                        streams_activos.append({
                            "name": name,
                            "time": stream.find("time").text,
                            "bw_video": stream.find("bw_video").text
                        })

        return {"is_live": len(streams_activos) > 0, "active_streams": streams_activos}
    except Exception as e:
        return {"is_live": False, "error": str(e)}

@app.get("/files/{filename}/thumbnail")
async def get_video_thumbnail(filename: str):
    RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
    file_path = os.path.join(RUTA_VIDEOS, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video no encontrado")

    duration = await get_raw_duration(file_path)
    mid_point = duration / 2

    command = [
        'ffmpeg', '-ss', str(mid_point), '-i', file_path,
        '-frames:v', '1', '-q:v', '15', '-f', 'image2', 'pipe:1'
    ]

    def stream_thumbnail():
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while chunk := process.stdout.read(4096):
                yield chunk
        finally:
            process.terminate()
            process.wait()

    return StreamingResponse(stream_thumbnail(), media_type="image/jpeg")

@app.get("/files")
async def get_files(date: Optional[str] = Query(None)):
    try:
        RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
        if not os.path.exists(RUTA_VIDEOS):
            raise HTTPException(status_code=404, detail="La ruta no existe.")
        
        contents = os.listdir(RUTA_VIDEOS)
        file_list = []
        for item in contents:
            if not item.endswith('.mp4'): continue
            item_path = os.path.join(RUTA_VIDEOS, item)
            mtime = os.path.getmtime(item_path)
            file_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            
            if date and file_date != date: continue
                
            duration = await get_video_duration_formatted(item_path)
            file_list.append({
                "name": item,
                "size": format_size(os.path.getsize(item_path)),
                "modified": format_modified_time(mtime),
                "duration": duration
            })
            
        file_list.sort(key=lambda x: x["modified"], reverse=True)
        return {"contents": file_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/files/videos-by-date")
async def get_videos_count_by_date():
    RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
    try:
        files = [f for f in os.listdir(RUTA_VIDEOS) if f.endswith('.mp4')]
        dates = [datetime.fromtimestamp(os.path.getmtime(os.path.join(RUTA_VIDEOS, f))).strftime("%Y-%m-%d") for f in files]
        counts = dict(Counter(dates))
        return dict(sorted(counts.items(), reverse=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/files/{filename}")
async def get_file(filename: str, request: Request, range: str = Header(None)):
    RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
    file_path = os.path.join(RUTA_VIDEOS, filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video no encontrado")

    file_size = os.path.getsize(file_path)
    
    if not range:
        async def video_stream():
            async with aiofiles.open(file_path, mode="rb") as file:
                while chunk := await file.read(1024 * 1024):
                    yield chunk
        return StreamingResponse(video_stream(), media_type="video/mp4", headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)})

    try:
        range_value = range.replace("bytes=", "")
        start_str, end_str = range_value.split("-")
        start = int(start_str)
        end = int(end_str) if end_str else file_size - 1
    except ValueError:
        raise HTTPException(status_code=416, detail="Rango no válido")

    if start >= file_size:
        raise HTTPException(status_code=416, detail="Rango fuera de límites")

    chunk_size = (end - start) + 1
    
    def get_video_chunk(start_pos, length):
        with open(file_path, mode="rb") as f:
            f.seek(start_pos)
            yield f.read(length)

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(chunk_size),
    }

    return StreamingResponse(get_video_chunk(start, chunk_size), status_code=206, media_type="video/mp4", headers=headers)