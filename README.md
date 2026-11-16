# 🎓 Asistencia automática

Herramienta de línea de comandos para **llevar el control de tu asistencia en la universidad** sin esfuerzo:

- 📅 Ve qué clases tienes hoy.
- ✅ Registra tu asistencia en segundos (`presente`, `ausente`, `justificada`, `tarde`).
- 📊 Calcula tu porcentaje por materia y **cuántas faltas te quedan** antes de reprobar por inasistencia.
- 🔔 Te avisa unos minutos antes de cada clase y **abre automáticamente el link de asistencia** del campus virtual (si tu universidad usa uno).
- ⏰ Si una clase terminó y no registraste nada, te pregunta **“¿Marcaste en …?”**.
- 📲 Genera una **página para el celular** con un botón “Marcar” que abre el link de la clase que tienes ahora.
- 📱 Exporta tu horario a un calendario `.ics` con alarmas para Google Calendar, iPhone u Outlook.

No necesita instalar nada: solo **Python 3.9+** (usa únicamente la biblioteca estándar).

## Instalación

```bash
git clone https://github.com/nicolas19273/asistencia-autom-tica-claude.git
cd asistencia-autom-tica-claude
cp config.ejemplo.json config.json   # luego edítalo con tus materias
```

## Configuración (`config.json`)

```jsonc
{
  "asistencia_minima": 75,               // % mínimo por defecto
  "link_asistencia": "https://...",      // la página donde marcas (la misma para todas las materias)
  "semestre": {
    "inicio": "2026-08-10",
    "fin": "2026-12-04",
    "feriados": ["2026-09-18"]           // días sin clases
  },
  "recordatorios": {
    "minutos_antes": 10,                 // aviso antes de clase (abre el link)
    "abrir_link": true,
    "verificar_minutos_despues": 5       // aviso "¿marcaste?" tras la clase; null = desactivado
  },
  "materias": [
    {
      "nombre": "Cálculo II",
      "aula": "Edificio B - Sala 204",   // opcional
      "asistencia_minima": 80,           // opcional, sobrescribe el global
      "link": "https://...",             // opcional: solo si esta materia se marca en otra página
      "clases_totales": 32,              // opcional: si no, se calcula con las fechas del semestre
      "tarde_cuenta_como": "presente",   // o "ausente"
      "horarios": [
        { "dia": "lunes", "inicio": "08:30", "fin": "10:00" }
      ]
    }
  ]
}
```

Los días aceptan tildes o no (`miércoles` / `miercoles`). Tu `config.json` y tu registro (`data/registro.csv`) están en `.gitignore`, así que no se suben a GitHub.

## Uso

```bash
python asistencia.py hoy                        # clases de hoy
python asistencia.py marcar-hoy                 # te pregunta por cada clase de hoy (Enter = presente)
python asistencia.py marcar "calculo" ausente   # marca una materia (acepta nombres parciales)
python asistencia.py marcar ingles justificada --fecha 2026-09-04 --nota "certificado médico"
python asistencia.py resumen                    # porcentajes y faltas restantes
python asistencia.py historial programacion     # últimos registros de una materia
python asistencia.py recordar                   # avisa antes de cada clase y después si no marcaste
python asistencia.py abrir                      # abre el link de asistencia de la clase actual
python asistencia.py pagina                     # crea asistencia.html con botones para el celular
python asistencia.py exportar-ics -o horario.ics
```

Ejemplo de `resumen`:

```
Materia       Pres  Aus  Just     %   Mín  Faltas libres  Estado
--------------------------------------------------------------------
Cálculo II      12    1     0   92.3    80            5/6  ✅ OK
Programación    14    2     1   87.5    75            6/8  ✅ OK
Inglés           5    3     0   62.5    75            1/4  ⚠️  PELIGRO
```

## Automatizarlo

### Recordatorios al encender el computador

**Linux/macOS (cron)** — revisa cada 5 minutos en días de semana:

```cron
*/5 7-21 * * 1-5 cd /ruta/a/asistencia-autom-tica-claude && python3 asistencia.py recordar --una-vez
```

> Con `--una-vez`, ajusta `minutos_antes` a un valor ≥ 5 para que ningún aviso se pierda entre ejecuciones. Los avisos ya enviados se guardan en `data/.avisos_enviados`, así que no se repiten.

**Windows (Programador de tareas)** — crea una tarea que al iniciar sesión ejecute:

```
pythonw.exe C:\ruta\a\asistencia-autom-tica-claude\asistencia.py recordar
```

### Marcar con un toque desde el celular

**1. Página con botones** (la forma más rápida):

```bash
python asistencia.py pagina
```

Pasa `asistencia.html` a tu celular (por Google Drive, iCloud o envíatela por correo) y ábrela. La clase que tienes ahora aparece arriba en verde con el botón **Marcar**, que abre directo el link de asistencia.

- **iPhone:** guárdala en *Archivos* y crea un atajo en la app *Atajos* → “Abrir archivo” → *Añadir a pantalla de inicio*.
- **Android:** ábrela en Chrome → menú ⋮ → *Agregar a la pantalla principal*.

**2. Alarmas en tu calendario:**

```bash
python asistencia.py exportar-ics -o horario.ics
```

Importa `horario.ics` en Google Calendar (Configuración → Importar y exportar) o ábrelo en el iPhone. Cada clase trae dos alarmas: una antes de empezar y otra al inicio diciendo “Marca tu asistencia”, y el evento incluye el link.

**3. Atajo automático en iPhone** (opcional): en *Atajos → Automatización → Hora del día*, crea una por cada horario de clase con la acción *Abrir URL* (el link de asistencia de esa materia). A esa hora te aparece la notificación y con un toque quedas en la página para marcar.

**En Android** puedes hacer lo mismo con la app *Rutinas* de Samsung/Google o con *Tasker*: disparador = hora de la clase, acción = abrir el link.

## Tests

```bash
python -m unittest discover -s tests -v
```

Se ejecutan automáticamente en GitHub Actions en cada push.

## Nota

Esta herramienta te ayuda a **no olvidar** registrar tu asistencia y a saber cómo vas; no marca asistencia por ti en el sistema de la universidad. Abre el link a la hora de la clase para que tú lo registres estando presente.
