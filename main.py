from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from collections import Counter
import os
import aiofiles
from dotenv import load_dotenv
from datetime import datetime
import subprocess

from fastapi import FastAPI, HTTPException, Query
import httpx  # Necesario para consultar las estadísticas de Nginx
import xml.etree.ElementTree as ET # Para procesar la respuesta de Nginx
from typing import Optional


load_dotenv()


app = FastAPI()

# --- Funciones auxiliares para formato de datos ---
def format_size(size_in_bytes: int):
    """Convierte el tamaño en bytes a un formato legible (KB, MB, GB)."""
    
    #print(size_in_bytes)
    
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
    """Convierte un timestamp de UNIX a un string de fecha y hora legible."""
    if timestamp is None:
        return "N/A"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

async def get_video_duration(file_path: str) -> str:
    """
    Obtiene la duración de un archivo de video usando ffprobe.
    Retorna la duración en segundos como una cadena o "N/A" si falla.
    """
    try:
        # Comando para ffprobe para extraer la duración del video
        command = [
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        
        # Ejecuta el comando y captura la salida
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        duration_seconds = float(result.stdout.strip())
        
        # Formatea la duración para mostrar horas, minutos y segundos
        hours = int(duration_seconds // 3600)
        minutes = int((duration_seconds % 3600) // 60)
        seconds = int(duration_seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError) as e:
        # Maneja errores si ffprobe no está instalado o si el archivo no es un video válido
        print(f"Error al obtener la duración de {file_path}: {e}")
        return "N/A"
    #-------- FIN DE FUNCIONES AUXILIARES ------- 


@app.get("/")
async def root():
    """ Responde con un mensaje de bienvenida
    
    Returns: 
        dict: Un diccionario con el mensaje de bienvenida
    Notes: 
        Este es el mensaje de bienvenida de la API.
    """
    return {"message": "Hello World"}

@app.get("/stream-status")
async def get_stream_status():
    """
    Consulta las estadísticas de Nginx RTMP para saber si hay una transmisión activa.
    Requiere que el modulo rtmp_stat esté habilitado en Nginx (puerto 80).
    """
    try:
        # Intentamos obtener el XML de estadísticas de Nginx (usualmente en puerto 80)
        async with httpx.AsyncClient() as client:
            # Cambia esta URL si tu servidor de estadísticas está en otro path o puerto
            response = await client.get("http://localhost/stat", timeout=2.0)
            
        if response.status_code != 200:
            return {"is_live": False, "message": "No se pudo conectar con el servidor de estadísticas"}

        root = ET.fromstring(response.content)
        
        # Buscamos streams activos dentro de la aplicación 'live'
        streams_activos = []
        for app in root.findall(".//app"):
            app_name = app.find("name").text
            if app_name == "live":
                for stream in app.findall(".//stream"):
                    name = stream.find("name").text
                    # Un stream está activo si tiene un 'publisher' (alguien enviando video)
                    publisher = stream.find("publisher")
                    if publisher is not None:
                        streams_activos.append({
                            "name": name,
                            "time": stream.find("time").text, # Tiempo en ms que lleva activo
                            "bw_video": stream.find("bw_video").text, # Ancho de banda video
                            "client_id": publisher.find("clientid").text
                        })

        return {
            "is_live": len(streams_activos) > 0,
            "active_streams": streams_activos,
            "server_time": datetime.now().strftime("%H:%M:%S")
        }

    except Exception as e:
        # Fallback manual: Si las estadísticas fallan, podemos verificar si existe el archivo HLS temporal
        # Esto es menos preciso pero sirve como respaldo
        hls_path = "/var/www/html/stream/hls/norte.m3u8"
        if os.path.exists(hls_path):
            return {"is_live": True, "method": "file_fallback", "name": "norte"}
            
        return {"is_live": False, "error": str(e)}

@app.get("/files/{filename}/preview")
async def get_video_preview(filename: str, seconds: int = 3):
    """
    Extrae los primeros N segundos. 
    Optimizado para evitar crashes en reproductores móviles (media_kit/mpv).
    """
    file_path = os.path.join(RUTA_VIDEOS, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video no encontrado")

    # Ajustes en el comando FFmpeg:
    # -an: Elimina el audio para el preview (evita problemas de sincronización y reduce peso)
    # -c:v copy: Si el origen es h264, no recodificamos (es instantáneo)
    # -f ismv: Formato MP4 fragmentado más estable para pipes
    command = [
        'ffmpeg',
        '-ss', '00:00:00',
        '-i', file_path,
        '-t', str(seconds),
        '-c:v', 'copy', 
        '-an', 
        '-f', 'mp4',
        '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
        'pipe:1'
    ]

    def stream_preview():
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                chunk = process.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            process.terminate()
            process.wait()

    return StreamingResponse(stream_preview(), media_type="video/mp4")
    
@app.get("/files")
async def get_files(date: Optional[str] = Query(None, description="Filtrar por fecha en formato YYYY-MM-DD")):
    """
    Lista los archivos con sus metadatos. 
    Si se proporciona el parámetro 'date', filtra los resultados.
    """
    try:
        RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
        if not os.path.exists(RUTA_VIDEOS):
            raise HTTPException(status_code=404, detail="La ruta no existe.")
        
        contents = os.listdir(RUTA_VIDEOS)
        file_list = []
        
        for item in contents:
            item_path = os.path.join(RUTA_VIDEOS, item)
            
            # Solo procesar archivos mp4
            if not os.path.isfile(item_path) or not item.endswith('.mp4'):
                continue
            
            # Obtener fecha de modificación para el filtro
            mtime = os.path.getmtime(item_path)
            file_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            
            # Aplicar filtro si el parámetro date existe
            if date and file_date != date:
                continue
                
            duration = await get_video_duration(item_path)
            file_list.append({
                "name": item,
                "is_directory": False,
                "size": format_size(os.path.getsize(item_path)),
                "modified": format_modified_time(mtime),
                "duration" : duration
            })
            
        # Ordenar por los más recientes primero
        file_list.sort(key=lambda x: x["modified"], reverse=True)
        
        return {"contents": file_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/files/videos-by-date")
async def get_videos_count_by_date():
    """
    Escanea el directorio de medios y devuelve un conteo de videos agrupados por fecha.
    Formato de respuesta: {"YYYY-MM-DD": cantidad}
    """
    RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
    
    if not os.path.exists(RUTA_VIDEOS):
        raise HTTPException(status_code=404, detail="Directorio de medios no encontrado")

    try:
        files = [f for f in os.listdir(RUTA_VIDEOS) if f.endswith('.mp4')]
        dates = []

        for filename in files:
            file_path = os.path.join(RUTA_VIDEOS, filename)
            # Obtenemos la fecha de modificación del archivo
            mtime = os.path.getmtime(file_path)
            # Convertimos a formato YYYY-MM-DD para agrupar
            date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
            dates.append(date_str)

        # Counter crea un diccionario con las frecuencias: {'2026-02-10': 10, '2026-02-11': 2}
        counts = dict(Counter(dates))
        
        # Opcional: Ordenar por fecha descendente
        ordered_counts = dict(sorted(counts.items(), reverse=True))
        
        return ordered_counts

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al procesar estadísticas: {str(e)}")

    
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


    
                
