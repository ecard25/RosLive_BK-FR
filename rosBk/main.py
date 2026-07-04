from datetime import datetime, time, timezone
import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, status, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import httpx
from dotenv import load_dotenv

# Carga de variables
load_dotenv(override=True)

DYTE_ORGANIZATION_ID = os.getenv("DYTE_ORGANIZATION_ID")
DYTE_API_KEY = os.getenv("DYTE_API_KEY")
CLOUDFLARE_APP_ID = os.getenv("CLOUDFLARE_APP_ID")

# --- VARIABLE GLOBAL PARA PERSISTIR LA SALA ---
SALA_ID_UNICA = None

from database import init_db, get_db, UsuarioModel, PeticionModel, SalaModel, Rol, TableroModel
from security import (
    obtener_hash_contrasena, verificar_contrasena, 
    crear_token_acceso, obtener_usuario_actual,
    AccesoDenegadoException, exigir_roles
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from database import engine
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS nombre_real VARCHAR;"))
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS rol VARCHAR(10) DEFAULT 'usuario' NOT NULL;"))
            conn.commit()
            print("Columnas nombre_real y rol verificadas/añadidas con éxito.")
            
            # Auto-promoción de roles basada en el nombre de usuario para facilitar pruebas y migración
            conn.execute(text("""
                UPDATE usuarios 
                SET rol = 'admin' 
                WHERE (usuario LIKE '%admin%' OR usuario = 'aaa') AND rol = 'usuario';
            """))
            conn.execute(text("""
                UPDATE usuarios 
                SET rol = 'moderador' 
                WHERE (usuario LIKE '%profesor%' OR usuario LIKE '%docente%') AND rol = 'usuario';
            """))
            conn.commit()
            print("Roles auto-promocionados basados en nombre de usuario con éxito.")
    except Exception as e:
        print(f"Error al verificar/añadir columnas o actualizar roles: {e}")
    yield

app = FastAPI(title="Sala Virtual Backend", lifespan=lifespan)

@app.exception_handler(AccesoDenegadoException)
async def acceso_denegado_exception_handler(request: Request, exc: AccesoDenegadoException):
    return JSONResponse(
        status_code=403,
        content={"success": False, "message": exc.message}
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class UsuarioPeticion(BaseModel):
    usuario: str
    contrasena: str
    nombre_real: Optional[str] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    nombre_real: Optional[str] = None
    rol: str

class SalaPeticion(BaseModel):
    client_specific_id: str
    name: str
    preset_name: str
    title: Optional[str] = None
    clase_id: Optional[int] = None

class PeticionCrear(BaseModel):
    peticion: str = Field(..., max_length=150)

class PeticionResponse(BaseModel):
    id: int
    peticion: str
    usuario_id: int
    fecha_hora: datetime
    valido: int

    model_config = {
        "from_attributes": True
    }

class EstadoHoyResponse(BaseModel):
    ya_envio: bool
    peticion: Optional[str] = None

@app.post("/api/usuarios/registro", status_code=status.HTTP_201_CREATED)
def registrar_usuario(usuario_data: UsuarioPeticion, db: Session = Depends(get_db)):
    if db.query(UsuarioModel).filter(UsuarioModel.usuario == usuario_data.usuario).first():
        raise HTTPException(status_code=400, detail="Usuario en uso.")
    hash_password = obtener_hash_contrasena(usuario_data.contrasena)
    nuevo_usuario = UsuarioModel(
        usuario=usuario_data.usuario, 
        contrasena_hash=hash_password,
        nombre_real=usuario_data.nombre_real
    )
    db.add(nuevo_usuario)
    db.commit()
    return {"mensaje": "Usuario registrado"}

@app.post("/api/usuarios/login", response_model=LoginResponse)
def login_usuario(usuario_data: UsuarioPeticion, db: Session = Depends(get_db)):
    usuario_db = db.query(UsuarioModel).filter(UsuarioModel.usuario == usuario_data.usuario).first()
    if not usuario_db or not verificar_contrasena(usuario_data.contrasena, usuario_db.contrasena_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")
    token = crear_token_acceso(data={"sub": usuario_db.usuario})
    return {
        "access_token": token, 
        "token_type": "bearer",
        "nombre_real": usuario_db.nombre_real,
        "rol": usuario_db.rol
    }

@app.get("/api/clase/verificar-acceso")
def verificar_acceso(usuario_actual: UsuarioModel = Depends(obtener_usuario_actual)):
    return {
        "mensaje": "Acceso verificado", 
        "usuario": usuario_actual.usuario,
        "nombre_real": usuario_actual.nombre_real,
        "rol": usuario_actual.rol
    }

@app.post("/api/peticiones", response_model=PeticionResponse, status_code=status.HTTP_201_CREATED)
def crear_peticion(
    peticion_data: PeticionCrear,
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(obtener_usuario_actual)
):
    nueva_peticion = PeticionModel(
        peticion=peticion_data.peticion,
        usuario_id=usuario_actual.id
    )
    db.add(nueva_peticion)
    db.commit()
    db.refresh(nueva_peticion)
    return nueva_peticion

@app.get("/api/peticiones/estado_hoy", response_model=EstadoHoyResponse)
def verificar_estado_hoy(
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(obtener_usuario_actual)
):
    ahora_local = datetime.now().astimezone()
    tz = ahora_local.tzinfo
    hoy_local = ahora_local.date()
    
    inicio_local = datetime.combine(hoy_local, time.min, tzinfo=tz)
    fin_local = datetime.combine(hoy_local, time.max, tzinfo=tz)
    
    inicio_dia = inicio_local.astimezone(timezone.utc).replace(tzinfo=None)
    fin_dia = fin_local.astimezone(timezone.utc).replace(tzinfo=None)

    peticion_hoy = db.query(PeticionModel).filter(
        PeticionModel.usuario_id == usuario_actual.id,
        PeticionModel.fecha_hora >= inicio_dia,
        PeticionModel.fecha_hora <= fin_dia
    ).order_by(PeticionModel.fecha_hora.desc()).first()

    if peticion_hoy:
        return {
            "ya_envio": True,
            "peticion": peticion_hoy.peticion
        }
    return {
        "ya_envio": False,
        "peticion": None
    }

@app.get("/api/peticiones/hoy")
def obtener_peticiones_hoy(
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(obtener_usuario_actual)
):
    ahora_local = datetime.now().astimezone()
    tz = ahora_local.tzinfo
    hoy_local = ahora_local.date()
    
    inicio_local = datetime.combine(hoy_local, time.min, tzinfo=tz)
    fin_local = datetime.combine(hoy_local, time.max, tzinfo=tz)
    
    inicio_dia = inicio_local.astimezone(timezone.utc).replace(tzinfo=None)
    fin_dia = fin_local.astimezone(timezone.utc).replace(tzinfo=None)

    # Consulta uniendo peticiones con el nombre de usuario
    peticiones = db.query(PeticionModel, UsuarioModel.usuario).join(
        UsuarioModel, PeticionModel.usuario_id == UsuarioModel.id
    ).filter(
        PeticionModel.fecha_hora >= inicio_dia,
        PeticionModel.fecha_hora <= fin_dia
    ).order_by(PeticionModel.fecha_hora.asc()).all()

    return [
        {
            "peticion": p.PeticionModel.peticion,
            "usuario": p.usuario
        }
        for p in peticiones
    ]

# --- FUNCIÓN DE SALA ÚNICA INTEGRADA ---
@app.post("/api/clase/crear-sala")
async def crear_sala(
    sala_data: SalaPeticion,
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(obtener_usuario_actual)
):
    global SALA_ID_UNICA
    
    # 1. Buscar si hay alguna sala activa en la base de datos (PostgreSQL es la fuente de verdad)
    sala_activa = db.query(SalaModel).filter(SalaModel.activa == 1).order_by(SalaModel.fecha_creacion.desc()).first()
    if sala_activa:
        SALA_ID_UNICA = sala_activa.meeting_id
        print(f"--- Reutilizando sala activa de la BD: {SALA_ID_UNICA} ---")
    else:
        SALA_ID_UNICA = None
        print("--- No hay sala activa en la BD ---")

    # 2. Control de acceso: si no hay sala activa, solo admin o moderador pueden crearla
    if SALA_ID_UNICA is None:
        if usuario_actual.rol not in ["admin", "moderador"]:
            raise AccesoDenegadoException("La sala aún no esta disponible.")

    headers = {
        "Authorization": f"Bearer {DYTE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        intentos = 0
        while intentos < 2:
            intentos += 1
            
            # A. Si hay una sala activa asignada, validar su estado real en Cloudflare
            if SALA_ID_UNICA is not None:
                meeting_check_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings/{SALA_ID_UNICA}"
                try:
                    check_res = await client.get(meeting_check_url, headers=headers)
                    meeting_valido = False
                    if check_res.is_success:
                        meeting_info = check_res.json().get("data", {})
                        if meeting_info.get("status") == "ACTIVE":
                            meeting_valido = True
                    
                    if not meeting_valido:
                        print(f"--- Sala {SALA_ID_UNICA} no activa en Cloudflare (status != ACTIVE). Limpiando de BD. ---")
                        sala_db = db.query(SalaModel).filter(SalaModel.meeting_id == SALA_ID_UNICA).first()
                        if sala_db:
                            sala_db.activa = 0
                            db.commit()
                        SALA_ID_UNICA = None
                except Exception as e:
                    print(f"--- Error al verificar la sala {SALA_ID_UNICA} en Cloudflare: {e} ---")
                    SALA_ID_UNICA = None
            
            # B. Si no hay sala activa (o si se acaba de invalidar arriba)
            if SALA_ID_UNICA is None:
                if usuario_actual.rol not in ["admin", "moderador"]:
                    raise AccesoDenegadoException("La sala aún no esta disponible.")
                
                meetings_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings"
                meeting_response = await client.post(meetings_url, json={"title": sala_data.title or "Sala"}, headers=headers)
                
                if not meeting_response.is_success:
                    print(f"--- Error al crear sala en Cloudflare: {meeting_response.status_code} - {meeting_response.text} ---")
                    raise HTTPException(status_code=500, detail="Error de Cloudflare al crear la reunión.")
                
                meeting_data = meeting_response.json()
                SALA_ID_UNICA = meeting_data["data"]["id"]
                print(f"--- Sala nueva creada en Cloudflare: {SALA_ID_UNICA} ---")
                
                # Registrar en la base de datos
                nueva_sala = SalaModel(
                    meeting_id=SALA_ID_UNICA,
                    usuario_id=usuario_actual.id,
                    activa=1
                )
                db.add(nueva_sala)
                db.commit()
                db.refresh(nueva_sala)
            
            meeting_id = SALA_ID_UNICA
            
            # C. Agregar participante con restricciones de solo audio
            participants_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings/{meeting_id}/participants"
            participant_payload = {
                "name": sala_data.name,
                "preset_name": sala_data.preset_name,
                "client_specific_id": sala_data.client_specific_id,
                "permissions": {
                    "media": {
                        "audio": True,
                        "video": False,
                        "screenshare": False
                    }
                }
            }
            
            p_res = await client.post(participants_url, json=participant_payload, headers=headers)
            
            if p_res.is_success:
                return {
                    "authToken": p_res.json()["data"]["token"], 
                    "meeting_id": meeting_id
                }
            else:
                # Si falló (ej. la reunión ya no es válida o caducó en Cloudflare)
                print(f"--- Error al agregar participante a {meeting_id}: {p_res.status_code} - {p_res.text} ---")
                
                # Marcar la reunión como inactiva en PostgreSQL
                sala_db = db.query(SalaModel).filter(SalaModel.meeting_id == meeting_id).first()
                if sala_db:
                    sala_db.activa = 0
                    db.commit()
                    print(f"--- Sala {meeting_id} desactivada en BD por vencimiento en Cloudflare ---")
                
                # Resetear la variable local/global para que en la siguiente iteración se cree una nueva sala
                SALA_ID_UNICA = None
                
                # Si no es admin/moderador, no puede crear salas, lanzamos error inmediatamente
                if usuario_actual.rol not in ["admin", "moderador"]:
                    raise AccesoDenegadoException("La sala aún no esta disponible.")

@app.post("/api/sala/forzar_cierre")
async def forzar_cierre(
    meeting_id: Optional[str] = None,
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin", "moderador"))
):
    global SALA_ID_UNICA
    
    if meeting_id:
        sala_activa = db.query(SalaModel).filter(
            SalaModel.meeting_id == meeting_id
        ).first()
        if not sala_activa:
            raise HTTPException(
                status_code=404,
                detail=f"No se encontró la sala {meeting_id} en la base de datos."
            )
    else:
        # Buscar la última sala activa
        sala_activa = db.query(SalaModel).filter(
            SalaModel.activa == 1
        ).order_by(SalaModel.fecha_creacion.desc()).first()
        
        if not sala_activa:
            SALA_ID_UNICA = None
            raise HTTPException(
                status_code=404,
                detail="No hay ninguna sala activa registrada en la base de datos."
            )
        
    meeting_id_to_close = sala_activa.meeting_id
    if SALA_ID_UNICA == meeting_id_to_close:
        SALA_ID_UNICA = None
        
    # Usar el endpoint de Cloudflare Realtime Kit, ya que el token de API es de Cloudflare
    cf_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings/{meeting_id_to_close}"
    headers = {
        "Authorization": f"Bearer {DYTE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.patch(cf_url, json={"status": "INACTIVE"}, headers=headers)
            
            # Validación estricta del estado de respuesta exitosa
            if response.status_code in [200, 201, 204]:
                ahora_utc = datetime.utcnow()
                duracion_segundos = int((ahora_utc - sala_activa.fecha_creacion).total_seconds())
                
                sala_activa.fecha_terminacion = ahora_utc
                sala_activa.duracion = duracion_segundos
                sala_activa.activa = 0
                db.commit()
                
                return {
                    "status": "success",
                    "message": "Sala cerrada correctamente",
                    "mensaje": "Sesiones purgadas con éxito.",
                    "meeting_id": meeting_id_to_close,
                    "cloudflare_status": response.status_code
                }
            else:
                db.rollback()
                status_err = response.status_code
                if status_err in [401, 403]:
                    msg = f"Error de autenticación con la API de videoconferencia ({status_err})"
                    raise HTTPException(status_code=status_err, detail=msg)
                elif status_err == 404:
                    ahora_utc = datetime.utcnow()
                    duracion_segundos = int((ahora_utc - sala_activa.fecha_creacion).total_seconds())
                    
                    sala_activa.fecha_terminacion = ahora_utc
                    sala_activa.duracion = duracion_segundos
                    sala_activa.activa = 0
                    db.commit()
                    return {
                        "status": "success",
                        "message": "La sala no existía en el servidor (404), pero fue desactivada localmente en la base de datos.",
                        "mensaje": "La sala no existía en el servidor (404), pero fue desactivada localmente en la base de datos.",
                        "meeting_id": meeting_id_to_close,
                        "cloudflare_status": response.status_code
                    }
                else:
                    msg = f"Error al cerrar la sesión en la API de videoconferencia ({status_err})"
                    raise HTTPException(status_code=status_err, detail=msg)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Error de conexión con la API de videoconferencia: {str(e)}"
            )

@app.get("/api/sala/estado/{meeting_id}")
def verificar_estado_sala(meeting_id: str, db: Session = Depends(get_db)):
    sala = db.query(SalaModel).filter(SalaModel.meeting_id == meeting_id).first()
    if not sala:
        return {"activa": 0}
    return {"activa": sala.activa}

@app.get("/api/admin/monitoreo_realtime")
async def monitoreo_realtime(
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin"))
):
    cf_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings"
    headers = {
        "Authorization": f"Bearer {DYTE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # 1. Obtener listado de reuniones
            response = await client.get(cf_url, headers=headers)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Error al listar reuniones en Cloudflare: {response.text}")
            
            meetings = response.json().get("data", [])
            active_meetings = [m for m in meetings if m.get("status") == "ACTIVE"]
            
            total_participantes = 0
            detalles_salas = []
            
            # 2. Para cada reunión activa, consultar participantes
            for m in active_meetings:
                m_id = m.get("id")
                part_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings/{m_id}/participants"
                p_res = await client.get(part_url, headers=headers)
                
                num_participants = 0
                if p_res.status_code == 200:
                    participants_data = p_res.json().get("data", [])
                    num_participants = len(participants_data)
                    total_participantes += num_participants
                
                detalles_salas.append({
                    "meeting_id": m_id,
                    "title": m.get("title", "Sala sin título"),
                    "created_at": m.get("created_at"),
                    "participantes_activos": num_participants
                })
                
            return {
                "salas_activas_en_cloudflare": len(active_meetings),
                "total_participantes_activos": total_participantes,
                "detalles": detalles_salas
            }
            
        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"Error de red al conectar con Cloudflare: {str(e)}")

@app.post("/api/admin/purga_total_cloudflare")
async def purga_total_cloudflare(
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin"))
):
    global SALA_ID_UNICA
    
    cf_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings"
    headers = {
        "Authorization": f"Bearer {DYTE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            # 1. Obtener listado de todas las reuniones
            response = await client.get(cf_url, headers=headers)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=f"Error al listar reuniones en Cloudflare: {response.text}")
            
            meetings = response.json().get("data", [])
            active_meetings = [m for m in meetings if m.get("status") == "ACTIVE"]
            
            salas_destruidas = 0
            
            # 2. Recorrer y purgar cada una de ellas
            for m in active_meetings:
                meeting_id = m.get("id")
                patch_url = f"https://api.cloudflare.com/client/v4/accounts/{DYTE_ORGANIZATION_ID}/realtime/kit/{CLOUDFLARE_APP_ID}/meetings/{meeting_id}"
                patch_res = await client.patch(patch_url, json={"status": "INACTIVE"}, headers=headers)
                
                if patch_res.status_code in [200, 201, 204]:
                    salas_destruidas += 1
                    
                    # Desactivar también en base de datos local en caso de que exista
                    sala_db = db.query(SalaModel).filter(
                        SalaModel.meeting_id == meeting_id, 
                        SalaModel.activa == 1
                    ).first()
                    
                    if sala_db:
                        ahora_utc = datetime.utcnow()
                        sala_db.fecha_terminacion = ahora_utc
                        sala_db.duracion = int((ahora_utc - sala_db.fecha_creacion).total_seconds())
                        sala_db.activa = 0
            
            db.commit()
            
            # 3. Limpiar variable en memoria
            SALA_ID_UNICA = None
            
            return {
                "status": "success",
                "message": "Purga masiva completada",
                "salas_destruidas": salas_destruidas
            }
            
        except httpx.RequestError as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Error de red al conectar con Cloudflare para la purga: {str(e)}")

# --- NUEVA FUNCIONALIDAD: TABLERO DINÁMICO ---
import shutil
import uuid

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "rosFr", "assets", "sala")
os.makedirs(UPLOAD_DIR, exist_ok=True)

class TextoPeticion(BaseModel):
    contenido: str
    ubicacion: str = "centro"

@app.post("/api/tablero/upload")
async def upload_tablero_imagen(
    file: UploadFile = File(...),
    ubicacion: str = Form(...), # "izquierda" o "centro"
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin", "moderador"))
):
    if ubicacion not in ["izquierda", "centro"]:
        raise HTTPException(status_code=400, detail="Ubicación inválida. Debe ser 'izquierda' o 'centro'.")
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido. Solo imágenes.")

    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar la imagen en disco: {str(e)}")

    nuevo_recurso = TableroModel(
        ubicacion=ubicacion,
        tipo="imagen",
        contenido=f"assets/sala/{unique_filename}",
        fechaCarga=datetime.utcnow(),
        usuarioCarga=usuario_actual.id,
        valido=1,
        activoActual=0
    )
    db.add(nuevo_recurso)
    db.commit()
    db.refresh(nuevo_recurso)

    return {
        "success": True,
        "id": nuevo_recurso.id,
        "ubicacion": nuevo_recurso.ubicacion,
        "tipo": nuevo_recurso.tipo,
        "contenido": nuevo_recurso.contenido,
        "activoActual": nuevo_recurso.activoActual
    }

@app.post("/api/tablero/texto")
async def create_tablero_texto(
    payload: TextoPeticion,
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin", "moderador"))
):
    if payload.ubicacion != "centro":
        raise HTTPException(status_code=400, detail="El texto solo se puede destinar a la card del 'centro'.")
    
    nuevo_recurso = TableroModel(
        ubicacion="centro",
        tipo="texto",
        contenido=payload.contenido,
        fechaCarga=datetime.utcnow(),
        usuarioCarga=usuario_actual.id,
        valido=1,
        activoActual=0
    )
    db.add(nuevo_recurso)
    db.commit()
    db.refresh(nuevo_recurso)

    return {
        "success": True,
        "id": nuevo_recurso.id,
        "ubicacion": nuevo_recurso.ubicacion,
        "tipo": nuevo_recurso.tipo,
        "contenido": nuevo_recurso.contenido,
        "activoActual": nuevo_recurso.activoActual
    }

@app.get("/api/tablero/recursos")
async def get_tablero_recursos(
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin", "moderador"))
):
    recursos = db.query(TableroModel).filter(TableroModel.valido == 1).order_by(TableroModel.fechaCarga.desc()).all()
    return [
        {
            "id": r.id,
            "ubicacion": r.ubicacion,
            "tipo": r.tipo,
            "contenido": r.contenido,
            "fechaCarga": r.fechaCarga.isoformat(),
            "activoActual": r.activoActual
        }
        for r in recursos
    ]

@app.post("/api/tablero/activar/{id}")
async def activar_tablero_recurso(
    id: int,
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin", "moderador"))
):
    recurso = db.query(TableroModel).filter(TableroModel.id == id, TableroModel.valido == 1).first()
    if not recurso:
        raise HTTPException(status_code=404, detail="Recurso no encontrado o inválido.")

    # Desactivar todos los recursos para esa misma ubicación
    db.query(TableroModel).filter(
        TableroModel.ubicacion == recurso.ubicacion,
        TableroModel.valido == 1
    ).update({TableroModel.activoActual: 0}, synchronize_session=False)

    # Activar el recurso seleccionado
    recurso.activoActual = 1
    db.commit()

    return {"success": True, "message": f"Recurso {id} activado para la ubicación '{recurso.ubicacion}'"}

@app.post("/api/tablero/eliminar/{id}")
async def eliminar_tablero_recurso(
    id: int,
    db: Session = Depends(get_db),
    usuario_actual: UsuarioModel = Depends(exigir_roles("admin", "moderador"))
):
    recurso = db.query(TableroModel).filter(TableroModel.id == id, TableroModel.valido == 1).first()
    if not recurso:
        raise HTTPException(status_code=404, detail="Recurso no encontrado.")
    
    recurso.valido = 0
    recurso.activoActual = 0
    db.commit()
    return {"success": True, "message": "Recurso eliminado del tablero."}

@app.get("/api/tablero/activos")
async def get_tablero_activos(
    db: Session = Depends(get_db)
):
    izquierda = db.query(TableroModel).filter(
        TableroModel.ubicacion == "izquierda",
        TableroModel.activoActual == 1,
        TableroModel.valido == 1
    ).first()

    centro = db.query(TableroModel).filter(
        TableroModel.ubicacion == "centro",
        TableroModel.activoActual == 1,
        TableroModel.valido == 1
    ).first()

    return {
        "izquierda": {
            "id": izquierda.id,
            "tipo": izquierda.tipo,
            "contenido": izquierda.contenido
        } if izquierda else None,
        "centro": {
            "id": centro.id,
            "tipo": centro.tipo,
            "contenido": centro.contenido
        } if centro else None
    }