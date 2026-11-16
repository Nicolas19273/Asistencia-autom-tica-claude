#!/usr/bin/env python3
"""Asistencia automática: control y recordatorios de asistencia universitaria.

Uso rápido:
    python asistencia.py hoy                 # clases de hoy
    python asistencia.py marcar-hoy          # registra (preguntando) las clases de hoy
    python asistencia.py marcar "Cálculo" presente
    python asistencia.py resumen             # porcentajes y faltas que te quedan
    python asistencia.py recordar            # avisa antes de cada clase (y abre el link)
    python asistencia.py abrir               # abre el link de asistencia de la clase actual
    python asistencia.py pagina              # página con botones para el celular
    python asistencia.py exportar-ics        # calendario con alarmas para tu celular

Solo usa la biblioteca estándar de Python 3.9+.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import platform
import shutil
import subprocess
import sys
import time
import unicodedata
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CONFIG_POR_DEFECTO = RAIZ / "config.json"
REGISTRO_POR_DEFECTO = RAIZ / "data" / "registro.csv"
AVISOS_ENVIADOS = RAIZ / "data" / ".avisos_enviados"

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
ESTADOS = ("presente", "ausente", "justificada", "tarde")
CAMPOS_CSV = ["fecha", "materia", "estado", "nota"]


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres de días y materias."""
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).strip().lower()


def parsear_hora(texto: str) -> tuple[int, int]:
    h, m = texto.split(":")
    return int(h), int(m)


# --------------------------------------------------------------------------- #
# Modelo
# --------------------------------------------------------------------------- #
@dataclass
class Horario:
    dia: int  # 0 = lunes
    inicio: str  # "HH:MM"
    fin: str


@dataclass
class Materia:
    nombre: str
    horarios: list[Horario]
    asistencia_minima: float = 75.0
    clases_totales: int | None = None
    link: str | None = None
    aula: str | None = None
    tarde_cuenta_como: str = "presente"

    def clases_del_dia(self, dia: date) -> list[Horario]:
        return [h for h in self.horarios if h.dia == dia.weekday()]


@dataclass
class Config:
    materias: list[Materia]
    inicio_semestre: date | None = None
    fin_semestre: date | None = None
    feriados: set[date] = field(default_factory=set)
    minutos_aviso: int = 10
    abrir_link: bool = True
    minutos_despues: int | None = 5  # aviso "¿marcaste?" tras terminar la clase; None = desactivado

    def buscar_materia(self, nombre: str) -> Materia:
        objetivo = normalizar(nombre)
        exactas = [m for m in self.materias if normalizar(m.nombre) == objetivo]
        if exactas:
            return exactas[0]
        parciales = [m for m in self.materias if objetivo in normalizar(m.nombre)]
        if len(parciales) == 1:
            return parciales[0]
        if not parciales:
            raise SystemExit(f"No encontré la materia '{nombre}'. Revisa config.json.")
        nombres = ", ".join(m.nombre for m in parciales)
        raise SystemExit(f"'{nombre}' es ambiguo: {nombres}")

    def es_dia_lectivo(self, dia: date) -> bool:
        if dia in self.feriados:
            return False
        if self.inicio_semestre and dia < self.inicio_semestre:
            return False
        if self.fin_semestre and dia > self.fin_semestre:
            return False
        return True

    def clases_de(self, dia: date) -> list[tuple[Materia, Horario]]:
        if not self.es_dia_lectivo(dia):
            return []
        clases = [(m, h) for m in self.materias for h in m.clases_del_dia(dia)]
        return sorted(clases, key=lambda par: par[1].inicio)

    def sesiones_en_semestre(self, materia: Materia) -> int | None:
        """Total de sesiones: el valor explícito o el calculado con las fechas del semestre."""
        if materia.clases_totales:
            return materia.clases_totales
        if not (self.inicio_semestre and self.fin_semestre):
            return None
        total, dia = 0, self.inicio_semestre
        while dia <= self.fin_semestre:
            if self.es_dia_lectivo(dia):
                total += len(materia.clases_del_dia(dia))
            dia += timedelta(days=1)
        return total


def cargar_config(ruta: Path) -> Config:
    if not ruta.exists():
        raise SystemExit(
            f"No existe {ruta}. Copia config.ejemplo.json a config.json y edítalo con tus materias."
        )
    with ruta.open(encoding="utf-8") as f:
        datos = json.load(f)

    materias = []
    for m in datos.get("materias", []):
        horarios = []
        for h in m.get("horarios", []):
            dia = normalizar(h["dia"])
            if dia not in DIAS:
                raise SystemExit(f"Día inválido '{h['dia']}' en {m['nombre']}. Usa: {', '.join(DIAS)}")
            parsear_hora(h["inicio"])
            parsear_hora(h["fin"])
            horarios.append(Horario(DIAS.index(dia), h["inicio"], h["fin"]))
        materias.append(
            Materia(
                nombre=m["nombre"],
                horarios=horarios,
                asistencia_minima=float(m.get("asistencia_minima", datos.get("asistencia_minima", 75))),
                clases_totales=m.get("clases_totales"),
                link=m.get("link"),
                aula=m.get("aula"),
                tarde_cuenta_como=m.get("tarde_cuenta_como", "presente"),
            )
        )

    sem = datos.get("semestre", {})
    fecha = lambda s: date.fromisoformat(s) if s else None  # noqa: E731
    recordatorios = datos.get("recordatorios", {})
    return Config(
        materias=materias,
        inicio_semestre=fecha(sem.get("inicio")),
        fin_semestre=fecha(sem.get("fin")),
        feriados={date.fromisoformat(d) for d in sem.get("feriados", [])},
        minutos_aviso=int(recordatorios.get("minutos_antes", 10)),
        abrir_link=bool(recordatorios.get("abrir_link", True)),
        minutos_despues=recordatorios.get("verificar_minutos_despues", 5),
    )


# --------------------------------------------------------------------------- #
# Registro (CSV)
# --------------------------------------------------------------------------- #
def leer_registro(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    with ruta.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def guardar_registro(ruta: Path, filas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    filas = sorted(filas, key=lambda r: (r["fecha"], r["materia"]))
    with ruta.open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=CAMPOS_CSV)
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({k: fila.get(k, "") for k in CAMPOS_CSV})


def registrar(ruta: Path, materia: str, estado: str, dia: date, nota: str = "", sesiones_del_dia: int = 1) -> None:
    """Agrega una marca. Si ya están todas las sesiones de ese día, reemplaza la última."""
    if estado not in ESTADOS:
        raise SystemExit(f"Estado inválido '{estado}'. Usa: {', '.join(ESTADOS)}")
    filas = leer_registro(ruta)
    mismas = [i for i, r in enumerate(filas) if r["fecha"] == dia.isoformat() and r["materia"] == materia]
    nueva = {"fecha": dia.isoformat(), "materia": materia, "estado": estado, "nota": nota}
    if len(mismas) >= max(1, sesiones_del_dia):
        filas[mismas[-1]] = nueva
    else:
        filas.append(nueva)
    guardar_registro(ruta, filas)


# --------------------------------------------------------------------------- #
# Estadísticas
# --------------------------------------------------------------------------- #
@dataclass
class Resumen:
    materia: str
    presentes: int
    ausentes: int
    justificadas: int
    porcentaje: float | None
    minimo: float
    total_semestre: int | None
    faltas_permitidas: int | None
    faltas_restantes: int | None

    @property
    def estado(self) -> str:
        if self.faltas_restantes is not None:
            if self.faltas_restantes < 0:
                return "PERDIDA"
            if self.faltas_restantes <= 1:
                return "PELIGRO"
            return "OK"
        if self.porcentaje is None:
            return "SIN DATOS"
        return "OK" if self.porcentaje >= self.minimo else "PELIGRO"


def calcular_resumen(config: Config, filas: list[dict]) -> list[Resumen]:
    resultados = []
    for m in config.materias:
        presentes = ausentes = justificadas = 0
        for r in filas:
            if r["materia"] != m.nombre:
                continue
            estado = r["estado"]
            if estado == "tarde":
                estado = m.tarde_cuenta_como
            if estado == "presente":
                presentes += 1
            elif estado == "ausente":
                ausentes += 1
            elif estado == "justificada":
                justificadas += 1
        contadas = presentes + ausentes  # las justificadas no cuentan en contra
        porcentaje = 100 * presentes / contadas if contadas else None
        total = config.sesiones_en_semestre(m)
        permitidas = restantes = None
        if total:
            base = total - justificadas
            permitidas = math.floor(base * (100 - m.asistencia_minima) / 100 + 1e-9)
            restantes = permitidas - ausentes
        resultados.append(
            Resumen(m.nombre, presentes, ausentes, justificadas, porcentaje, m.asistencia_minima, total, permitidas, restantes)
        )
    return resultados


# --------------------------------------------------------------------------- #
# Notificaciones
# --------------------------------------------------------------------------- #
def notificar(titulo: str, mensaje: str) -> None:
    print(f"\a🔔 {titulo}: {mensaje}", flush=True)
    sistema = platform.system()
    try:
        if sistema == "Darwin":
            script = f'display notification {json.dumps(mensaje)} with title {json.dumps(titulo)}'
            subprocess.run(["osascript", "-e", script], check=False)
        elif sistema == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", titulo, mensaje], check=False)
        elif sistema == "Windows":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$n=New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                f"$n.ShowBalloonTip(10000,{json.dumps(titulo)},{json.dumps(mensaje)},'Info');"
                "Start-Sleep 10"
            )
            subprocess.Popen(["powershell", "-NoProfile", "-Command", ps])
    except OSError:
        pass  # si falla la notificación del sistema, al menos quedó el mensaje en consola


# --------------------------------------------------------------------------- #
# Calendario .ics
# --------------------------------------------------------------------------- #
def _escapar_ics(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def generar_ics(config: Config, desde: date, hasta: date) -> str:
    sello = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lineas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//asistencia-automatica//ES", "CALSCALE:GREGORIAN"]
    dia = desde
    while dia <= hasta:
        for materia, h in config.clases_de(dia):
            hi, mi = parsear_hora(h.inicio)
            hf, mf = parsear_hora(h.fin)
            inicio = datetime.combine(dia, datetime.min.time()).replace(hour=hi, minute=mi)
            fin = datetime.combine(dia, datetime.min.time()).replace(hour=hf, minute=mf)
            descripcion = "Recuerda registrar tu asistencia."
            if materia.link:
                descripcion += f"\nLink: {materia.link}"
            lineas += [
                "BEGIN:VEVENT",
                f"UID:{uuid.uuid5(uuid.NAMESPACE_URL, f'{materia.nombre}-{inicio.isoformat()}')}@asistencia",
                f"DTSTAMP:{sello}",
                f"DTSTART:{inicio:%Y%m%dT%H%M%S}",
                f"DTEND:{fin:%Y%m%dT%H%M%S}",
                f"SUMMARY:{_escapar_ics(materia.nombre)}",
                f"DESCRIPTION:{_escapar_ics(descripcion)}",
            ]
            if materia.aula:
                lineas.append(f"LOCATION:{_escapar_ics(materia.aula)}")
            if materia.link:
                lineas.append(f"URL:{materia.link}")
            lineas += [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escapar_ics(materia.nombre)} empieza pronto",
                f"TRIGGER:-PT{config.minutos_aviso}M",
                "END:VALARM",
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:Marca tu asistencia en {_escapar_ics(materia.nombre)}",
                "TRIGGER:PT0M",
                "END:VALARM",
                "END:VEVENT",
            ]
        dia += timedelta(days=1)
    lineas.append("END:VCALENDAR")
    return "\r\n".join(lineas) + "\r\n"


# --------------------------------------------------------------------------- #
# Comandos
# --------------------------------------------------------------------------- #
def cmd_hoy(config: Config, args) -> None:
    dia = args.fecha or date.today()
    clases = config.clases_de(dia)
    if not clases:
        print(f"{dia} ({DIAS[dia.weekday()]}): no tienes clases. 🎉")
        return
    print(f"Clases del {dia} ({DIAS[dia.weekday()]}):")
    for m, h in clases:
        extra = f" · {m.aula}" if m.aula else ""
        link = f"\n      {m.link}" if m.link else ""
        print(f"  {h.inicio}-{h.fin}  {m.nombre}{extra}{link}")


def cmd_marcar(config: Config, args) -> None:
    materia = config.buscar_materia(args.materia)
    dia = args.fecha or date.today()
    sesiones = len(materia.clases_del_dia(dia)) or 1
    registrar(args.registro, materia.nombre, args.estado, dia, args.nota or "", sesiones)
    print(f"✔ {materia.nombre} — {dia}: {args.estado}")


def cmd_marcar_hoy(config: Config, args) -> None:
    dia = args.fecha or date.today()
    clases = config.clases_de(dia)
    if not clases:
        print("Hoy no tienes clases que registrar.")
        return
    atajos = {"p": "presente", "a": "ausente", "j": "justificada", "t": "tarde", "": "presente"}
    print("Responde: [p]resente (Enter), [a]usente, [j]ustificada, [t]arde, [s]altar")
    for m, h in clases:
        respuesta = normalizar(input(f"  {h.inicio} {m.nombre}: "))
        if respuesta == "s":
            continue
        estado = atajos.get(respuesta[:1], None)
        if estado is None:
            print("    (respuesta no reconocida, se omite)")
            continue
        registrar(args.registro, m.nombre, estado, dia, "", len(m.clases_del_dia(dia)))
    print("Listo ✔")


def cmd_resumen(config: Config, args) -> None:
    resumen = calcular_resumen(config, leer_registro(args.registro))
    ancho = max([len(r.materia) for r in resumen] + [7])
    print(f"{'Materia':<{ancho}}  Pres  Aus  Just     %   Mín  Faltas libres  Estado")
    print("-" * (ancho + 56))
    iconos = {"OK": "✅", "PELIGRO": "⚠️ ", "PERDIDA": "❌", "SIN DATOS": "·"}
    for r in resumen:
        pct = f"{r.porcentaje:5.1f}" if r.porcentaje is not None else "    -"
        libres = f"{r.faltas_restantes}/{r.faltas_permitidas}" if r.faltas_permitidas is not None else "-"
        print(
            f"{r.materia:<{ancho}}  {r.presentes:>4}  {r.ausentes:>3}  {r.justificadas:>4}  {pct}  "
            f"{r.minimo:>4.0f}  {libres:>13}  {iconos[r.estado]} {r.estado}"
        )
    if any(r.faltas_permitidas is None for r in resumen):
        print("\nTip: define 'semestre.inicio' y 'semestre.fin' (o 'clases_totales') para saber cuántas faltas te quedan.")


@dataclass
class Aviso:
    tipo: str  # "antes" o "verificar"
    materia: Materia
    horario: Horario
    clave: str


VENTANA_VERIFICAR_MIN = 60


def avisos_pendientes(config: Config, ahora: datetime, filas: list[dict], ya_enviados: set[str]) -> list[Aviso]:
    """Calcula qué avisos tocan en este momento.

    - "antes": la clase empieza dentro de `minutos_aviso` minutos.
    - "verificar": la clase terminó hace `minutos_despues` minutos (hasta 1 h) y
      no hay ningún registro de esa materia para hoy.
    """
    hoy = ahora.date()
    registradas = {r["materia"] for r in filas if r["fecha"] == hoy.isoformat()}
    avisos = []
    for m, h in config.clases_de(hoy):
        hi, mi = parsear_hora(h.inicio)
        hf, mf = parsear_hora(h.fin)
        inicio = datetime.combine(hoy, datetime.min.time()).replace(hour=hi, minute=mi)
        fin = datetime.combine(hoy, datetime.min.time()).replace(hour=hf, minute=mf)

        clave = f"antes|{m.nombre}|{inicio.isoformat()}"
        faltan = (inicio - ahora).total_seconds() / 60
        if clave not in ya_enviados and 0 <= faltan <= config.minutos_aviso:
            avisos.append(Aviso("antes", m, h, clave))

        if config.minutos_despues is None or m.nombre in registradas:
            continue
        clave = f"verificar|{m.nombre}|{inicio.isoformat()}"
        pasados = (ahora - fin).total_seconds() / 60 - config.minutos_despues
        if clave not in ya_enviados and 0 <= pasados <= VENTANA_VERIFICAR_MIN:
            avisos.append(Aviso("verificar", m, h, clave))
    return avisos


def _leer_enviados(ruta: Path) -> set[str]:
    if not ruta.exists():
        return set()
    hoy = date.today().isoformat()
    # solo se conservan los de hoy, para que el archivo no crezca
    return {l for l in ruta.read_text(encoding="utf-8").splitlines() if hoy in l}


def _guardar_enviados(ruta: Path, enviados: set[str]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("\n".join(sorted(enviados)), encoding="utf-8")


def cmd_recordar(config: Config, args) -> None:
    """Avisa antes de cada clase (abriendo el link) y después si no registraste asistencia."""
    enviados = _leer_enviados(AVISOS_ENVIADOS)
    if not args.una_vez:
        print(f"Recordatorios activos ({config.minutos_aviso} min antes). Ctrl+C para salir.")
    try:
        while True:
            filas = leer_registro(args.registro)
            for aviso in avisos_pendientes(config, datetime.now(), filas, enviados):
                m, h = aviso.materia, aviso.horario
                if aviso.tipo == "antes":
                    notificar(m.nombre, f"Empieza a las {h.inicio}. ¡Marca tu asistencia!")
                    if m.link and config.abrir_link and not args.sin_link:
                        webbrowser.open(m.link)
                else:
                    notificar(
                        f"¿Marcaste en {m.nombre}?",
                        f'No hay registro de hoy. Si fuiste, márcalo: python asistencia.py marcar "{m.nombre}"',
                    )
                enviados.add(aviso.clave)
                _guardar_enviados(AVISOS_ENVIADOS, enviados)
            if args.una_vez:
                break
            time.sleep(30)
    except KeyboardInterrupt:
        print("\nRecordatorios detenidos.")


def clase_actual_o_siguiente(config: Config, ahora: datetime) -> tuple[Materia, Horario] | None:
    """La clase en curso; si no hay, la próxima de hoy; si no, la última que terminó hoy."""
    clases = config.clases_de(ahora.date())
    hora = ahora.strftime("%H:%M")
    for m, h in clases:
        if h.inicio <= hora <= h.fin:
            return m, h
    siguientes = [(m, h) for m, h in clases if h.inicio > hora]
    if siguientes:
        return siguientes[0]
    return clases[-1] if clases else None


def cmd_abrir(config: Config, args) -> None:
    if args.materia:
        materia = config.buscar_materia(args.materia)
    else:
        par = clase_actual_o_siguiente(config, datetime.now())
        if not par:
            raise SystemExit("Hoy no tienes clases.")
        materia = par[0]
    if not materia.link:
        raise SystemExit(f"{materia.nombre} no tiene 'link' en config.json.")
    print(f"Abriendo asistencia de {materia.nombre}: {materia.link}")
    webbrowser.open(materia.link)


def generar_pagina(config: Config) -> str:
    """Página HTML para el celular: un botón por materia y la clase actual destacada."""
    import html as _html

    datos = [
        {
            "nombre": m.nombre,
            "link": m.link or "",
            "aula": m.aula or "",
            "horarios": [{"dia": h.dia, "inicio": h.inicio, "fin": h.fin} for h in m.horarios],
        }
        for m in config.materias
    ]
    plantilla = (RAIZ / "plantillas" / "pagina.html").read_text(encoding="utf-8")
    return plantilla.replace("__DATOS__", json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")).replace(
        "__TITULO__", _html.escape("Mi asistencia")
    )


def cmd_pagina(config: Config, args) -> None:
    args.salida.write_text(generar_pagina(config), encoding="utf-8")
    print(f"✔ Página creada en {args.salida}. Pásala a tu celular y agrégala a la pantalla de inicio.")


def cmd_exportar_ics(config: Config, args) -> None:
    desde = args.desde or config.inicio_semestre or date.today()
    hasta = args.hasta or config.fin_semestre or (desde + timedelta(days=120))
    contenido = generar_ics(config, desde, hasta)
    args.salida.write_text(contenido, encoding="utf-8")
    eventos = contenido.count("BEGIN:VEVENT")
    print(f"✔ {eventos} clases exportadas a {args.salida}. Impórtalo en Google Calendar / iPhone / Outlook.")


def cmd_historial(config: Config, args) -> None:
    filas = leer_registro(args.registro)
    if args.materia:
        nombre = config.buscar_materia(args.materia).nombre
        filas = [r for r in filas if r["materia"] == nombre]
    if not filas:
        print("Todavía no hay registros.")
        return
    for r in filas[-args.ultimos:]:
        nota = f"  ({r['nota']})" if r.get("nota") else ""
        print(f"{r['fecha']}  {r['estado']:<11}  {r['materia']}{nota}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Control y recordatorios de asistencia universitaria.")
    p.add_argument("--config", type=Path, default=CONFIG_POR_DEFECTO, help="ruta a config.json")
    p.add_argument("--registro", type=Path, default=REGISTRO_POR_DEFECTO, help="ruta al CSV de asistencia")
    sub = p.add_subparsers(dest="comando", required=True)

    s = sub.add_parser("hoy", help="muestra las clases de hoy")
    s.add_argument("--fecha", type=date.fromisoformat)
    s.set_defaults(func=cmd_hoy)

    s = sub.add_parser("marcar", help="registra asistencia de una materia")
    s.add_argument("materia")
    s.add_argument("estado", nargs="?", default="presente", choices=ESTADOS)
    s.add_argument("--fecha", type=date.fromisoformat, help="AAAA-MM-DD (por defecto hoy)")
    s.add_argument("--nota", help="comentario opcional")
    s.set_defaults(func=cmd_marcar)

    s = sub.add_parser("marcar-hoy", help="pregunta por cada clase de hoy")
    s.add_argument("--fecha", type=date.fromisoformat)
    s.set_defaults(func=cmd_marcar_hoy)

    s = sub.add_parser("resumen", help="porcentaje de asistencia y faltas restantes")
    s.set_defaults(func=cmd_resumen)

    s = sub.add_parser("historial", help="últimos registros")
    s.add_argument("materia", nargs="?")
    s.add_argument("-n", "--ultimos", type=int, default=20)
    s.set_defaults(func=cmd_historial)

    s = sub.add_parser("recordar", help="avisa antes de cada clase y abre el link de asistencia")
    s.add_argument("--sin-link", action="store_true", help="no abrir el navegador")
    s.add_argument("--una-vez", action="store_true", help="revisa una sola vez (útil con cron)")
    s.set_defaults(func=cmd_recordar)

    s = sub.add_parser("abrir", help="abre el link de asistencia (de la clase actual si no indicas materia)")
    s.add_argument("materia", nargs="?")
    s.set_defaults(func=cmd_abrir)

    s = sub.add_parser("pagina", help="crea una página para el celular con botones de asistencia")
    s.add_argument("-o", "--salida", type=Path, default=Path("asistencia.html"))
    s.set_defaults(func=cmd_pagina)

    s = sub.add_parser("exportar-ics", help="crea un calendario .ics con alarmas")
    s.add_argument("--desde", type=date.fromisoformat)
    s.add_argument("--hasta", type=date.fromisoformat)
    s.add_argument("-o", "--salida", type=Path, default=Path("horario.ics"))
    s.set_defaults(func=cmd_exportar_ics)
    return p


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = construir_parser().parse_args(argv)
    config = cargar_config(args.config)
    args.func(config, args)


if __name__ == "__main__":
    main()
