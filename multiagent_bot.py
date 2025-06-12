"""multiagent_bot.py — Multi-agente Tuya Billing Assistant
======================================================
Resuelve los requisitos de los Puntos 6, 7, 8 y 11 de la prueba.

Estructura de carpetas esperada en el repo
└── Punto6/prompts/agent_sofia.yaml
└── Punto7/prompts/{1_-4_*.yaml}
└── Punto8/prompts/{1_cot_unrecognized_charge.yaml,
                  2_direct_unrecognized_charge.yaml}
└── Punto11/prompts/1_auditoria_ia.yaml

Cada *.yaml define la parte [system] e instrucciones del agente.
Este script carga cada YAML, forma mensajes para la API de OpenAI
y expone clases con un método `.run()`.
"""
from __future__ import annotations

import json
import pathlib
import typing as t

import openai               # pip install openai>=1
from openai import OpenAI
from openai.types import ChatCompletion
from yaml import safe_load

# ---------- configuración ---------------------------
ROOT = pathlib.Path(__file__).resolve().parent
PROMPTS = {
    "p6": ROOT / "Punto6" / "prompts" / "agent_sofia.yaml",
    "p7_master": ROOT / "Punto7" / "prompts" / "1_prompt_maestro.yaml",
    "p7_parse": ROOT / "Punto7" / "prompts" / "2_parse_prompt.yaml",
    "p7_rag": ROOT / "Punto7" / "prompts" / "3_rag_prompt.yaml",
    "p7_reply": ROOT / "Punto7" / "prompts" / "4_sofia_responder.yaml",
    "p8_cot": ROOT / "Punto8" / "prompts" /
                 "1_cot_unrecognized_charge.yaml",
    "p8_direct": ROOT / "Punto8" / "prompts" /
                   "2_direct_unrecognized_charge.yaml",
    "p11_audit": ROOT / "Punto11" / "prompts" /
                  "1_auditoria_ia.yaml",
}
MODEL = "gpt-4o-mini"

# ---------- utilidades --------------------------------

def load_yaml(path: pathlib.Path) -> dict[str, str]:
    """Carga un archivo YAML y devuelve un dict."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {path}")
    return safe_load(path.read_text(encoding="utf‑8"))


def make_messages(prompt: dict[str, str], variables: dict[str, str]) -> list[dict]:
    """Sustituye placeholders {{var}} y construye los mensajes."""
    msg_system = prompt.get("system", "")
    for k, v in variables.items():
        placeholder = f"{{{{{k}}}}}"  # {{var}}
        msg_system = msg_system.replace(placeholder, v)
    msgs = [
        {"role": "system", "content": msg_system}
    ]
    # Otros campos opcionales (instructions, datos, pregunta)
    for role_key in ("instructions", "datos_extracto",
                     "pregunta_cliente", "json", "task"):
        if role_key in prompt:
            content = prompt[role_key]
            for k, v in variables.items():
                content = content.replace(f"{{{{{k}}}}}", v)
            msgs.append({"role": "user", "content": content})
    return msgs


class AgentBase:
    """Clase base: carga YAML, hace solicitud a la API y devuelve texto."""

    def __init__(self, yaml_path: pathlib.Path):
        self.yaml_path = yaml_path
        self.prompt_cfg = load_yaml(yaml_path)
        self.client = OpenAI()

    def run(self, **vars: str) -> str:
        messages = make_messages(self.prompt_cfg, vars)
        try:
            response: ChatCompletion = self.client.chat.completions.create(
                model=MODEL, messages=messages, temperature=0.2,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:                                # noqa: BLE001
            return f"⚠️ Error de la API: {exc}"

# ---------- agentes específicos -----------------------

class PromptBuilderAgent(AgentBase):
    """Punto 6: devuelve el prompt base de Sofía."""

    def __init__(self):
        super().__init__(PROMPTS["p6"])


class PDFExtractionAgent(AgentBase):
    """Punto 7: orquesta parse → RAG → respuesta utilizando tres YAML."""

    def __init__(self):
        self.parse = AgentBase(PROMPTS["p7_parse"])
        self.rag = AgentBase(PROMPTS["p7_rag"])
        self.reply = AgentBase(PROMPTS["p7_reply"])
        self.master = load_yaml(PROMPTS["p7_master"])
        self.client = OpenAI()

    def run(self, pdf_text: str, pregunta: str) -> str:
        # 1) parse JSON
        json_str = self.parse.run(PDF_CHUNK=pdf_text)
        # 2) RAG sobre JSON
        frag = self.rag.run(JSON_EXTRACT=json_str,
                           PREGUNTA_CLIENTE=pregunta)
        # 3) respuesta final
        respuesta = self.reply.run(FRAGMENTOS_RAG=frag,
                                   PREGUNTA_CLIENTE=pregunta,
                                   concepto="Cargo",
                                   explicacion="",
                                   calculo="",
                                   recomendacion="")
        return respuesta


class CoTExplainerAgent(AgentBase):
    """Punto 8: expone CoT y directo y permite comparar."""

    def __init__(self):
        self.cot = AgentBase(PROMPTS["p8_cot"])
        self.direct = AgentBase(PROMPTS["p8_direct"])

    def run(self, frag: str, pregunta: str) -> tuple[str, str]:
        """Devuelve (con_cot, sin_cot)."""
        with_cot = self.cot.run(FRAGMENTO_RAG=frag,
                                PREGUNTA_CLIENTE=pregunta,
                                concepto="Cargo")
        without_cot = self.direct.run(FRAGMENTO_RAG=frag,
                                      PREGUNTA_CLIENTE=pregunta,
                                      concepto="Cargo")
        return with_cot, without_cot


class AuditUsageAgent(AgentBase):
    """Punto 11: estima % de IA generativa en bloques de texto."""

    def __init__(self):
        super().__init__(PROMPTS["p11_audit"])

    def audit(self, respuestas: list[str]) -> str:
        joined = "\n\n".join(respuestas)
        return self.run(FECHA=str(date.today()),
                        LLM_NAME=MODEL,
                        RESPUESTAS_CANDIDATAS=joined)


# ---------- ejemplo de flujo --------------------------

def ejemplo() -> None:
    """Demostración end‑to‑end con capturas mínimas."""
    # 1) Construir prompt maestro Sofía
    p6 = PromptBuilderAgent()
    print("\n[P6] Prompt Base →", p6.run())

    # 2) Extraer info del PDF (usamos texto ficticio)
    pdf_agent = PDFExtractionAgent()
    fake_pdf = "Saldo anterior 800000 ... Compra Spotify 75100 ..."
    resp = pdf_agent.run(fake_pdf, "¿Por qué tengo un cobro de $75.100?")
    print("\n[P7] Respuesta del PDF →", resp)

    # 3) Comparar CoT vs directo
    cot_agent = CoTExplainerAgent()
    frag = "{ 'transacciones':[{'fecha':'2025-05-15', 'monto':75100, 'descripcion':'Spotify'}]}"
    cot, direct = cot_agent.run(frag, "¿Qué es el cargo de $75.100?")
    print("\n[P8] Con CoT →", cot)
    print("[P8] Directo →", direct)

    # 4) Auditor de IA
    auditor = AuditUsageAgent()
    audit_json = auditor.audit([cot, direct])
    print("\n[P11] Auditoría →", audit_json)


if __name__ == "__main__":  # test rápido
    ejemplo()
