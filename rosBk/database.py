from enum import Enum
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

# Configurar la URL de conexión de SQLAlchemy estrictamente con los datos proporcionados reales
SQLALCHEMY_DATABASE_URL = "postgresql://postgres:Qwertyuiop0.@52.70.42.210:5432/rosdb"

# Crear el motor de la base de datos de PostgreSQL
# No se utilizan argumentos exclusivos de SQLite (ej. check_same_thread)
engine = create_engine(SQLALCHEMY_DATABASE_URL)

# Crear la clase base declarativa para los modelos ORM
Base = declarative_base()

# Configurar el generador de sesiones para la conexión a la base de datos
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class Rol(str, Enum):
    admin = "admin"
    moderador = "moderador"
    usuario = "usuario"

# Definir el modelo ORM UsuarioModel mapeado a la tabla "usuarios"
class UsuarioModel(Base):
    __tablename__ = "usuarios"

    # ID: llave primaria, autoincrementable
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    
    # Usuario: string plano, único, indexado y no nulo. No valida email.
    usuario = Column(String, unique=True, index=True, nullable=False)
    
    # Contraseña en formato Hash: string no nulo para seguridad
    contrasena_hash = Column(String, nullable=False)
    
    # Nombre real del usuario
    nombre_real = Column(String, nullable=True)
    
    # Rol del usuario para RBAC
    rol = Column(String(10), default=Rol.usuario.value, nullable=False)

# Definir el modelo ORM PeticionModel mapeado a la tabla "peticiones"
class PeticionModel(Base):
    __tablename__ = "peticiones"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    peticion = Column(String(150), nullable=False)
    usuario_id = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    fecha_hora = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    valido = Column(Integer, default=1, nullable=False)

# Definir el modelo ORM SalaModel mapeado a la tabla "salas"
class SalaModel(Base):
    __tablename__ = "salas"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    meeting_id = Column(String, nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow, nullable=False)
    fecha_terminacion = Column(DateTime, nullable=True)
    duracion = Column(Integer, nullable=True)
    numero_participants = Column(Integer, default=0, nullable=True)
    activa = Column(Integer, default=1, nullable=False)

class TableroModel(Base):
    __tablename__ = "tablero"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ubicacion = Column(String(15), nullable=False)
    tipo = Column(String(10), nullable=False)
    contenido = Column(String, nullable=False)
    fechaCarga = Column(DateTime, default=datetime.utcnow, nullable=False)
    usuarioCarga = Column(Integer, ForeignKey('usuarios.id'), nullable=False)
    valido = Column(Integer, default=1, nullable=False)
    activoActual = Column(Integer, default=0, nullable=False)

def get_db():
    """
    Genera una sesión de base de datos para cada petición HTTP.
    Utiliza un bloque try/finally para asegurar la liberación correcta
    de las conexiones hacia Postgres (retornándolas al pool) tras finalizar.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    """
    Verifica la existencia de las tablas y las crea automáticamente
    en el esquema remoto si no existen al arrancar el servidor.
    """
    Base.metadata.create_all(bind=engine)
