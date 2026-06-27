from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from database import get_db, UsuarioModel

# Configuración global para la generación y firma de JSON Web Tokens (JWT)
SECRET_KEY = "TU_SECRET_KEY_SUPER_SEGURA_AQUI"  # Cambiar por una clave segura en producción
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30  # Expiración por defecto de 30 días

# OAuth2PasswordBearer extrae el token del header "Authorization: Bearer <token>"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/usuarios/login")

def obtener_hash_contrasena(contrasena: str) -> str:
    """
    Genera un hash seguro utilizando bcrypt nativo a partir de la contraseña plana.
    Compatible con Python 3.13+. Se utiliza durante el registro del usuario.
    """
    pwd_bytes = contrasena.encode('utf-8')
    salt = bcrypt.gensalt()
    hash_bytes = bcrypt.hashpw(pwd_bytes, salt)
    return hash_bytes.decode('utf-8')

def verificar_contrasena(contrasena_plana: str, contrasena_hash: str) -> bool:
    """
    Verifica si la contraseña ingresada en texto plano corresponde al hash
    almacenado en la base de datos utilizando bcrypt nativo.
    """
    pwd_bytes = contrasena_plana.encode('utf-8')
    hash_bytes = contrasena_hash.encode('utf-8')
    return bcrypt.checkpw(pwd_bytes, hash_bytes)

def crear_token_acceso(data: dict) -> str:
    """
    Genera un JWT firmado con el algoritmo HS256 y una SECRET_KEY.
    Define un tiempo de expiración (exp) para el token.
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def obtener_usuario_actual(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """
    Inyección de dependencias para validar el acceso en rutas protegidas.
    Descifra el JWT, verifica la firma y su vigencia (que no haya expirado), 
    busca el 'usuario' en Postgres y retorna la instancia del modelo ORM.
    Lanza HTTP 401 Unauthorized de forma inmediata en caso de fallo.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudieron validar las credenciales o el token expiró",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Descifrar y validar firma y caducidad del token (automáticamente valida 'exp')
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        usuario: str = payload.get("sub")  # El username plano se guarda bajo 'sub'
        if usuario is None:
            raise credentials_exception
    except jwt.PyJWTError:
        # Captura errores como InvalidTokenError y ExpiredSignatureError
        raise credentials_exception
        
    # Buscar el usuario directamente en Postgres por nombre de usuario (string plano)
    usuario_db = db.query(UsuarioModel).filter(UsuarioModel.usuario == usuario).first()
    if usuario_db is None:
        raise credentials_exception
        
    return usuario_db
    