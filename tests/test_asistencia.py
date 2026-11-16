import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asistencia as a  # noqa: E402

EJEMPLO = Path(__file__).resolve().parents[1] / "config.ejemplo.json"


class TestAsistencia(unittest.TestCase):
    def setUp(self):
        self.config = a.cargar_config(EJEMPLO)
        self.tmp = tempfile.TemporaryDirectory()
        self.registro = Path(self.tmp.name) / "registro.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_carga_dias_con_tilde(self):
        calculo = self.config.buscar_materia("calculo")
        self.assertEqual({h.dia for h in calculo.horarios}, {0, 2})  # lunes y miércoles

    def test_clases_de_un_lunes(self):
        clases = self.config.clases_de(date(2026, 8, 10))  # lunes
        self.assertEqual([m.nombre for m, _ in clases], ["Cálculo II"])

    def test_feriado_sin_clases(self):
        self.assertEqual(self.config.clases_de(date(2026, 10, 12)), [])

    def test_fuera_del_semestre(self):
        self.assertEqual(self.config.clases_de(date(2027, 1, 4)), [])

    def test_registrar_reemplaza_mismo_dia(self):
        dia = date(2026, 8, 10)
        a.registrar(self.registro, "Cálculo II", "ausente", dia)
        a.registrar(self.registro, "Cálculo II", "presente", dia)
        filas = a.leer_registro(self.registro)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["estado"], "presente")

    def test_resumen_y_faltas_restantes(self):
        a.registrar(self.registro, "Inglés", "presente", date(2026, 8, 14))
        a.registrar(self.registro, "Inglés", "ausente", date(2026, 8, 21))
        a.registrar(self.registro, "Inglés", "tarde", date(2026, 8, 28))  # cuenta como ausente
        a.registrar(self.registro, "Inglés", "justificada", date(2026, 9, 4))
        r = {x.materia: x for x in a.calcular_resumen(self.config, a.leer_registro(self.registro))}["Inglés"]
        self.assertEqual((r.presentes, r.ausentes, r.justificadas), (1, 2, 1))
        self.assertAlmostEqual(r.porcentaje, 100 / 3)
        # 16 clases - 1 justificada = 15; 25% de 15 = 3.75 -> 3 faltas permitidas
        self.assertEqual(r.faltas_permitidas, 3)
        self.assertEqual(r.faltas_restantes, 1)
        self.assertEqual(r.estado, "PELIGRO")

    def test_sesiones_calculadas_por_fechas(self):
        total = self.config.sesiones_en_semestre(self.config.buscar_materia("Programación"))
        self.assertGreater(total, 25)

    def test_ics(self):
        ics = a.generar_ics(self.config, date(2026, 8, 10), date(2026, 8, 16))
        self.assertEqual(ics.count("BEGIN:VEVENT"), 5)
        self.assertIn("TRIGGER:-PT10M", ics)
        self.assertIn("URL:https://campus.ejemplo.edu/asistencia/programacion", ics)

    def test_materia_inexistente(self):
        with self.assertRaises(SystemExit):
            self.config.buscar_materia("Química")


if __name__ == "__main__":
    unittest.main()
