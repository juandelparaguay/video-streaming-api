from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from collections import Counter
import os
import aiofiles
from dotenv import load_dotenv
from datetime import datetime
import subprocess


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


@app.get("/files")
async def get_files():
    try:
    
        RUTA_VIDEOS = os.getenv('RUTA_VIDEOS')
        
        # print(RUTA_VIDEOS)
        
        # full_path = os.path.abspath('files')  Obtiene la ruta absoluta para seguridad

        if not os.path.exists(RUTA_VIDEOS):
            raise HTTPException(status_code=404, detail=f"La ruta no existe.")

        if not os.path.isdir(RUTA_VIDEOS):
            raise HTTPException(status_code=400, detail=f"La ruta no es un directorio.")

        contents = os.listdir(RUTA_VIDEOS)
        file_list = []
        for item in contents:
            item_path = os.path.join(RUTA_VIDEOS, item)
            duration = await get_video_duration(item_path)
            
            print(os.path.getsize(item_path))
            
            file_info = {
                "name": item,
                "is_directory": os.path.isdir(item_path),
                "size": format_size(os.path.getsize(item_path)) if not os.path.isdir(item_path) else None,
                "modified": format_modified_time(os.path.getmtime(item_path)),
                "duration" : duration
                }
            file_list.append(file_info)

        return {"contents": file_list}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al explorar la carpeta: {str(e)}")
    
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


    
                
