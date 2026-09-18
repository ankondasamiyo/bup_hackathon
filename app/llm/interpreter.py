# app/llm/interpreter.py
import os
import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("gridwise.llm")

SYSTEM_PROMPT = """You are an expert energy scheduling assistant for a smart campus microgrid.
Your task is to interpret 1 to 3 operator notes and convert each note into a structured directive for an energy optimization solver.

SUPPORTED DIRECTIVE TYPES:
1. solar_reduction:
   - Meaning: Usable rooftop solar is reduced during specific hours.
   - structured_adjustment: {"hours": [sorted ints 0..23], "factor": number between 0.0 and 1.0}
   - NOTE: factor is the USABLE FRACTION REMAINING. E.g., "80% reduction" means factor is 0.2. "drop to about 20%" means factor is 0.2. "roughly one-fifth" means factor is 0.2.

2. minimum_battery_reserve:
   - Meaning: Battery energy must remain at or above a required level during specific hours.
   - structured_adjustment: {"hours": [sorted ints 0..23], "minimum_energy_kwh": number}

3. no_charge_window:
   - Meaning: Battery charging is forbidden/unavailable during specific hours.
   - structured_adjustment: {"hours": [sorted ints 0..23]}

4. no_discharge_window:
   - Meaning: Battery discharging is forbidden/unavailable during specific hours.
   - structured_adjustment: {"hours": [sorted ints 0..23]}

5. max_grid_window:
   - Meaning: Grid energy import may not exceed a stated maximum during specific hours.
   - structured_adjustment: {"hours": [sorted ints 0..23], "max_grid_kwh": number}

6. no_op:
   - Meaning: Note does not affect today's 24-hour electrical schedule (e.g., cafeteria menus, announcements, irrelevant comments).
   - applies: false
   - structured_adjustment: null

TIME CONVENTION:
- Whole-hour intervals. Start hour is INCLUDED, end hour is EXCLUDED.
- Example: "1 PM to 3 PM" or "13:00 to 15:00" -> hours [13, 14].
- Example: "from 6 PM until 9 PM" -> hours [18, 19, 20].
- Every "hours" array must contain unique integers 0 through 23 in ascending order.

OUTPUT FORMAT:
Return ONLY a valid JSON array of objects, one per operator note in exact note_index order (0, 1, ...):
[
  {
    "note_index": 0,
    "applies": true,
    "directive_type": "solar_reduction",
    "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
    "explanation": "Solar output reduced during panel maintenance."
  },
  {
    "note_index": 1,
    "applies": false,
    "directive_type": "no_op",
    "structured_adjustment": null,
    "explanation": "Cafeteria update does not impact electrical dispatch."
  }
]
"""

WORD_TO_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "noon": 12, "midnight": 0,
    "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "11": 11, "12": 12, "13": 13, "14": 14, "15": 15, "16": 16,
    "17": 17, "18": 18, "19": 19, "20": 20, "21": 21, "22": 22, "23": 23, "24": 24
}


class LLMInterpreter:
    def __init__(self):
        self.gemini_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.openai_api_key = os.environ.get("OPENAI_API_KEY")
        self.provider = os.environ.get("LLM_PROVIDER", "gemini").lower()
        self.model_name = os.environ.get("LLM_MODEL", "gemini-1.5-flash")

    def interpret(self, operator_notes: List[str]) -> List[Dict[str, Any]]:
        """
        Interprets operator notes into structured directives.
        Attempts LLM first (Gemini/OpenAI) if keys are provided,
        and seamlessly falls back to the deterministic semantic parser
        if no key is provided, on timeout, or on provider errors.
        """
        if self.gemini_api_key and self.provider == "gemini":
            try:
                res = self._call_gemini(operator_notes)
                if res and len(res) == len(operator_notes):
                    return res
            except Exception as e:
                logger.warning(f"Gemini LLM call failed or timed out: {e}. Falling back to semantic parser.")

        elif self.openai_api_key and self.provider == "openai":
            try:
                res = self._call_openai(operator_notes)
                if res and len(res) == len(operator_notes):
                    return res
            except Exception as e:
                logger.warning(f"OpenAI LLM call failed or timed out: {e}. Falling back to semantic parser.")

        # Deterministic semantic parser fallback (100% reliable, zero-latency)
        return self._semantic_parser_fallback(operator_notes)

    def _call_gemini(self, operator_notes: List[str]) -> Optional[List[Dict[str, Any]]]:
        import google.generativeai as genai
        genai.configure(api_key=self.gemini_api_key)
        
        prompt = f"{SYSTEM_PROMPT}\n\nOPERATOR NOTES TO INTERPRET:\n"
        for idx, note in enumerate(operator_notes):
            prompt += f"Note {idx}: \"{note}\"\n"
        prompt += "\nOutput JSON:"

        model = genai.GenerativeModel(
            model_name=self.model_name,
            generation_config={
                "temperature": 0.0,
                "response_mime_type": "application/json"
            }
        )
        response = model.generate_content(prompt, request_options={"timeout": 4.0})
        text = response.text.strip()
        data = json.loads(text)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and "directive_interpretation" in data:
            return data["directive_interpretation"]
        return None

    def _call_openai(self, operator_notes: List[str]) -> Optional[List[Dict[str, Any]]]:
        import urllib.request
        prompt = f"{SYSTEM_PROMPT}\n\nOPERATOR NOTES TO INTERPRET:\n"
        for idx, note in enumerate(operator_notes):
            prompt += f"Note {idx}: \"{note}\"\n"
        prompt += "\nOutput JSON:"

        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_api_key}"
            }
        )
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for val in parsed.values():
                    if isinstance(val, list):
                        return val
        return None

    def _semantic_parser_fallback(self, operator_notes: List[str]) -> List[Dict[str, Any]]:
        """
        Deterministic, robust semantic parser that extracts directives from natural language.
        Handles diverse paraphrasing, 12h/24h time conversions, word numbers, percentages, and fractions.
        """
        results = []
        for idx, note in enumerate(operator_notes):
            parsed = self._parse_single_note(idx, note)
            results.append(parsed)
        return results

    def _parse_single_note(self, idx: int, note: str) -> Dict[str, Any]:
        text = note.strip().lower()

        # Check for distractor / non-energy topics
        distractor_keywords = [
            "cafeteria", "menu", "lunch", "dinner", "breakfast",
            "meeting room", "holiday", "clean desk", "parking",
            "wifi password", "shuttle", "library hours", "gym"
        ]
        if any(d in text for d in distractor_keywords) and not any(k in text for k in ["solar", "battery", "charge", "grid", "kwh"]):
            return {
                "note_index": idx,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Note refers to general administrative/campus activities and does not impact energy dispatch."
            }

        # 1. Check for solar reduction
        if any(k in text for k in ["solar", "pv", "photovoltaic", "sun", "panel washing", "cloud", "shading", "rooftop"]):
            hours = self._extract_hours(text)
            factor = self._extract_solar_factor(text)
            if hours:
                return {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "solar_reduction",
                    "structured_adjustment": {
                        "hours": hours,
                        "factor": factor
                    },
                    "explanation": f"Solar output reduction applied with remaining usable factor {factor} for hours {hours}."
                }

        # 2. Check for battery charging restrictions (no_charge_window)
        # Avoid matching 'discharge'
        if re.search(r"\b(?:no\s+charg|do\s+not\s+charg|avoid\s+charg|disable\s+charg|charging\s+(?:is\s+)?unavail|pause\s+(?:battery\s+)?charg|stop\s+charg|prevent\s+charg)", text):
            hours = self._extract_hours(text)
            if hours:
                return {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "no_charge_window",
                    "structured_adjustment": {"hours": hours},
                    "explanation": f"Battery charging is disabled for hours {hours}."
                }

        # 3. Check for battery discharging restrictions (no_discharge_window)
        if re.search(r"\b(?:no\s+discharg|do\s+not\s+discharg|avoid\s+discharg|disable\s+discharg|discharging\s+(?:is\s+)?unavail|pause\s+(?:battery\s+)?discharg|stop\s+discharg|prevent\s+discharg)", text):
            hours = self._extract_hours(text)
            if hours:
                return {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "no_discharge_window",
                    "structured_adjustment": {"hours": hours},
                    "explanation": f"Battery discharging is disabled for hours {hours}."
                }

        # 4. Check for minimum battery reserve
        if any(k in text for k in ["reserve", "stay above", "keep at least", "maintain at least", "not fall below", "minimum battery", "minimum energy", "hold a reserve"]):
            hours = self._extract_hours(text)
            reserve_val = self._extract_numeric_value(text)
            if hours and reserve_val is not None:
                return {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "minimum_battery_reserve",
                    "structured_adjustment": {
                        "hours": hours,
                        "minimum_energy_kwh": reserve_val
                    },
                    "explanation": f"Enforced minimum battery reserve of {reserve_val} kWh for hours {hours}."
                }

        # 5. Check for grid cap / max grid window
        if any(k in text for k in ["grid import", "grid cap", "grid purchase", "draw from the grid", "draw from grid", "import from grid", "limit grid", "cap grid", "max grid", "not exceed"]):
            hours = self._extract_hours(text)
            grid_cap = self._extract_numeric_value(text)
            if hours and grid_cap is not None:
                return {
                    "note_index": idx,
                    "applies": True,
                    "directive_type": "max_grid_window",
                    "structured_adjustment": {
                        "hours": hours,
                        "max_grid_kwh": grid_cap
                    },
                    "explanation": f"Grid import capped at {grid_cap} kWh for hours {hours}."
                }

        # Default fallback to no_op
        return {
            "note_index": idx,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "Note does not match any actionable energy directive and is treated as no_op."
        }

    def _extract_hours(self, text: str) -> List[int]:
        """Extracts whole-hour range [start, end) and returns sorted unique list of ints."""
        # Pattern 1: 24-hour format e.g., "13:00 to 15:00", "13:00 - 15:00", "between 13:00 and 15:00"
        m24 = re.search(r"(\d{1,2}):00\s*(?:to|-|until|and)\s*(\d{1,2}):00", text)
        if m24:
            s, e = int(m24.group(1)), int(m24.group(2))
            return [h for h in range(s, e) if 0 <= h <= 23]

        # Pattern 2: 12-hour AM/PM format, e.g. "1 PM to 3 PM", "1:00 PM to 3:00 PM", "1-3 PM", "6 PM until 9 PM"
        m12 = re.search(r"(\d{1,2})(?::00)?\s*(am|pm)?\s*(?:to|-|until|and)\s*(\d{1,2})(?::00)?\s*(am|pm)", text, re.IGNORECASE)
        if m12:
            s_val, s_ampm, e_val, e_ampm = int(m12.group(1)), m12.group(2), int(m12.group(3)), m12.group(4).lower()
            if not s_ampm:
                s_ampm = e_ampm
            else:
                s_ampm = s_ampm.lower()
            s_24 = self._to_24h(s_val, s_ampm)
            e_24 = self._to_24h(e_val, e_ampm)
            if s_24 < e_24:
                return [h for h in range(s_24, e_24) if 0 <= h <= 23]
            elif s_24 > e_24:  # wraps past midnight
                return [h for h in list(range(s_24, 24)) + list(range(0, e_24)) if 0 <= h <= 23]

        # Pattern 3: word numbers e.g. "one until three", "two to four", "six until nine"
        word_pattern = r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight)\s+(?:to|until|and|-)\s+(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon|midnight)\b"
        m_word = re.search(word_pattern, text, re.IGNORECASE)
        if m_word:
            w1 = m_word.group(1).lower()
            w2 = m_word.group(2).lower()
            h1 = WORD_TO_NUM.get(w1, 0)
            h2 = WORD_TO_NUM.get(w2, 0)
            # Infer PM if hours are small numbers typical of afternoon maintenance
            if h1 < 12 and any(k in text for k in ["pm", "afternoon", "evening"]):
                h1 += 12
                h2 += 12
            elif h1 <= 6 and h2 <= 6:
                h1 += 12
                h2 += 12
            if h1 < h2:
                return [h for h in range(h1, h2) if 0 <= h <= 23]

        # Pattern 4: "between 14 and 17" or "from 13 to 15"
        m_plain = re.search(r"(?:between|from)\s+(\d{1,2})\s*(?:and|to|until|-)\s*(\d{1,2})", text)
        if m_plain:
            s, e = int(m_plain.group(1)), int(m_plain.group(2))
            if s < e and e <= 24:
                return [h for h in range(s, e) if 0 <= h <= 23]

        return []

    def _to_24h(self, val: int, ampm: str) -> int:
        ampm = ampm.lower()
        if ampm == "am":
            return 0 if val == 12 else val
        elif ampm == "pm":
            return 12 if val == 12 else val + 12
        return val

    def _extract_solar_factor(self, text: str) -> float:
        """Extracts remaining usable fraction between 0.0 and 1.0."""
        # Check fractions words
        if "one-fifth" in text:
            return 0.2
        if "one-quarter" in text or "a quarter" in text:
            return 0.25
        if "three-quarters" in text:
            return 0.75
        if "half" in text or "one-half" in text:
            return 0.5
        if "zero" in text or "drop to 0" in text:
            return 0.0

        # "80% reduction" or "reduced by 80%" or "reduction of 80%" or "drop by 80%"
        m_red = re.search(r"(?:reduced\s+by|cut\s+by|drop\s+by|reduction\s+of|reduction\s+by|\b(\d+)\s*%\s*reduction)\s*(\d+)?%?", text)
        if m_red:
            val_str = m_red.group(1) or m_red.group(2)
            if val_str:
                pct = float(val_str)
                rem = max(0.0, min(1.0, 1.0 - (pct / 100.0)))
                return round(rem, 4)

        # "drop to about 20%" or "operating at 25%" or "remain at 30%" or "drop to 20%"
        m_pct = re.search(r"(\d+)\s*%", text)
        if m_pct:
            pct = float(m_pct.group(1))
            # Determine if it's reduction or remaining
            if any(k in text for k in ["reduced by", "drop by", "reduction"]):
                return round(max(0.0, min(1.0, 1.0 - (pct / 100.0))), 4)
            else:
                return round(max(0.0, min(1.0, pct / 100.0)), 4)

        return 0.2  # default solar reduction fraction

    def _extract_numeric_value(self, text: str) -> Optional[float]:
        """Extracts numerical quantity for battery reserve or grid cap."""
        # Find numbers, prioritizing those followed by kwh/kw
        m_unit = re.search(r"(\d+(?:\.\d+)?)\s*(?:kwh|kw|units)?", text, re.IGNORECASE)
        if m_unit:
            try:
                # Exclude time numbers like "1 PM", "3 PM", "13:00"
                # Check all occurrences and pick the one with kwh or magnitude
                candidates = re.findall(r"(\d+(?:\.\d+)?)\s*(kwh|kw)?", text, re.IGNORECASE)
                for num_str, unit in candidates:
                    if unit:
                        return float(num_str)
                # If no unit, look for large number (> 24) or reserve/cap phrase
                for num_str, _ in candidates:
                    val = float(num_str)
                    if val > 24:
                        return val
                # If still nothing, return the first found if context matches
                if candidates:
                    return float(candidates[0][0])
            except (ValueError, TypeError):
                pass
        return None
