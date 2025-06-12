"""
multiagent_bot.py — Tuya Billing Assistant (PDF)
-------------------------------------------------
• Extrae texto de PDFs con Google Vision API.
• Selecciona y ejecuta el agente adecuado para los puntos 6, 7, 8 y 11.
• Usa prompts YAML desde carpetas Punto6, Punto7, etc.
• Requiere: openai, pyyaml, google-cloud-vision, python-dotenv
"""

import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any

import yaml
from openai import OpenAI, OpenAIError, RateLimitError
from google.cloud import vision_v1
from google.cloud.vision_v1 import types
from google.api_core.exceptions import GoogleAPIError
from dotenv import load_dotenv

# Carga variables de entorno
load_dotenv()

# --- UTILIDAD: OCR usando Google Vision API ---
def ocr_pdf_vision(pdf_path: Path) -> str:
    """Extrae texto de un PDF usando Google Cloud Vision API."""
    client = vision_v1.ImageAnnotatorClient()
    with pdf_path.open("rb") as f:
        content = f.read()
    input_config = types.InputConfig(
        content=content, mime_type="application/pdf"
    )
    features = [types.Feature(type_=vision_v1.Feature.Type.DOCUMENT_TEXT_DETECTION)]
    requests = [
        types.AnnotateFileRequest(
            input_config=input_config, features=features
        )
    ]
    response = client.batch_annotate_files(requests=requests)
    text = ""
    for resp in response.responses:
        for page_resp in resp.responses:
            if page_resp.full_text_annotation:
                text += page_resp.full_text_annotation.text + "\n"
    return text.strip()

# --- UTILIDAD: Cargar prompt YAML ---
def load_prompt_yaml(yaml_path: Path) -> Dict[str, Any]:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# --- UTILIDAD: Llama a OpenAI API ---
def call_openai(prompt: str, system_prompt: str = "", max_tokens: int = 400,
                temperature: float = 0.2) -> str:
    client = OpenAI()
    try:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content.strip()
    except RateLimitError:
        time.sleep(3)
        return call_openai(prompt, system_prompt, max_tokens, temperature)
    except OpenAIError as exc:
        return f"[OpenAI Error]: {exc}"

# --- AGENTE 1: Prompt Seguro para Facturación (Punto 6) ---
class SofiaAgent:
    """Agente que responde preguntas de facturación usando prompt seguro."""
    def __init__(self, prompt_yaml: Path):
        self.prompt_cfg = load_prompt_yaml(prompt_yaml)
        self.system = self.prompt_cfg.get("system", "")
        self.instructions = self.prompt_cfg.get("instructions", "")

    def run(self, datos_extracto: str, pregunta_cliente: str) -> str:
        prompt = (
            self.instructions +
            "\n[DATOS_EXTRACTO]\n" + datos_extracto +
            "\n[PREGUNTA_CLIENTE]\n" + pregunta_cliente
        )
        return call_openai(prompt, self.system)

# --- AGENTE 2: Pipeline PDF → RAG → Respuesta (Punto 7) ---
class PDFChainAgent:
    """Agente completo: OCR, parser, RAG, respuesta final Sofía."""
    def __init__(self, base_dir: Path):
        self.prompts = {
            "maestro": load_prompt_yaml(base_dir / "1_prompt_maestro.yaml"),
            "parser": load_prompt_yaml(base_dir / "2_parse_prompt.yaml"),
            "rag": load_prompt_yaml(base_dir / "3_rag_prompt.yaml"),
            "responder": load_prompt_yaml(base_dir / "4_sofia_responder.yaml"),
        }

    def run(self, pdf_path: Path, pregunta: str) -> str:
        texto = ocr_pdf_vision(pdf_path)
        # 1. Parsear el texto plano a JSON
        parser_tpl = self.prompts["parser"]
        parser_prompt = parser_tpl["system"] + "\n" + \
                        parser_tpl["objective"] + "\n" + \
                        parser_tpl["template"].replace("{{PDF_CHUNK}}", texto)
        datos_json = call_openai(parser_prompt, max_tokens=700)
        # 2. Seleccionar fragmentos con RAG
        rag_tpl = self.prompts["rag"]
        rag_prompt = rag_tpl["system"] + "\n" + rag_tpl["task"]
        rag_prompt = rag_prompt.replace("{{JSON_EXTRACT}}", datos_json)
        rag_prompt = rag_prompt.replace("{{PREGUNTA_CLIENTE}}", pregunta)
        fragmentos = call_openai(rag_prompt, max_tokens=400)
        # 3. Responder con Sofía
        responder_tpl = self.prompts["responder"]
        prompt_final = responder_tpl["instructions"] + \
                       "\n[DATOS_EXTRACTO]\n" + fragmentos + \
                       "\n[PREGUNTA_CLIENTE]\n" + pregunta
        return call_openai(prompt_final, responder_tpl.get("system", ""))

# --- AGENTE 3: Cargo no reconocido, con y sin CoT (Punto 8) ---
class ChargeExplainerAgent:
    """Explica cargos no reconocidos usando prompt CoT o directo."""
    def __init__(self, cot_yaml: Path, direct_yaml: Path):
        self.cot = load_prompt_yaml(cot_yaml)
        self.direct = load_prompt_yaml(direct_yaml)

    def run(self, modo: str, datos_extracto: str, pregunta: str) -> str:
        if modo == "cot":
            tpl = self.cot
            prompt = tpl["chain_of_thought"] + "\n" + tpl["instructions"]
            system = tpl["system"]
        else:
            tpl = self.direct
            prompt = tpl["instructions"]
            system = tpl["system"]
        prompt = prompt.replace("{{FRAGMENTO_RAG}}", datos_extracto)
        prompt = prompt.replace("{{PREGUNTA_CLIENTE}}", pregunta)
        return call_openai(prompt, system)

# --- AGENTE 4: Auditor uso de IA (Punto 11) ---
class AuditUsageAgent:
    """Evalúa si cada respuesta fue generada por IA."""
    def __init__(self, auditor_yaml: Path):
        self.tpl = load_prompt_yaml(auditor_yaml)

    def run(self, respuestas: List[str]) -> str:
        joined = "\n---\n".join(respuestas)
        prompt = self.tpl["instructions"].replace(
            "{{RESPUESTAS_CANDIDATAS}}", joined
        )
        return call_openai(prompt, self.tpl.get("system", ""), max_tokens=600)

# --- MAIN DEMO ---
def main():
    base = Path(__file__).parent
    pdf_dir = base / "PDF"
    # Demo: Punto 6
    print("Punto 6: Prompt seguro")
    sofia = SofiaAgent(base / "Punto_6/prompts/agent_sofia.yaml")
    datos = ocr_pdf_vision(pdf_dir / "Punto6_prompt_seguro.pdf")
    out6 = sofia.run(datos, "¿Por qué mi pago mínimo es tan alto?")
    print(out6, "\n")

    # Demo: Punto 7 (pipeline completo)
    print("Punto 7: Pipeline PDF → RAG → Sofía")
    agent7 = PDFChainAgent(base / "Punto_7/prompts/")
    out7 = agent7.run(pdf_dir / "Punto7_pipeline_completo.pdf",
                      "¿Por qué me cobraron seguro de compras?")
    print(out7, "\n")

    # Demo: Punto 8 (cargo no reconocido, CoT vs directo)
    print("Punto 8: Cargo no reconocido (CoT)")
    agent8 = ChargeExplainerAgent(
        base / "Punto_8/prompts/1_cot_unrecognized_charge.yaml",
        base / "Punto_8/prompts/2_direct_unrecognized_charge.yaml"
    )
    datos8 = ocr_pdf_vision(pdf_dir / "Punto8_cargo_no_reconocido.pdf")
    out8_cot = agent8.run("cot", datos8, "¿Qué es este cargo de $300.000?")
    print(out8_cot, "\n")
    print("Punto 8: Cargo no reconocido (directo)")
    out8_dir = agent8.run("direct", datos8, "¿Qué es este cargo de $300.000?")
    print(out8_dir, "\n")

    # Demo: Punto 11 (auditor de IA generativa)
    print("Punto 11: Auditor uso de IA generativa")
    auditor = AuditUsageAgent(base / "Punto11/prompts/1_auditoria_ia.yaml")
    out11 = auditor.run([out6, out7, out8_cot, out8_dir])
    print(out11, "\n")

if __name__ == "__main__":
    main()
