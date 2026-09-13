"""Explicit opt-in HTTPS JSON planning adapter; no SDK retry or database access.

Targets the configured DeepSeek chat-completions endpoint. The wire protocol
and model availability require a manual smoke test; automated tests stay offline.
"""

import http.client
import json
import os
from urllib.parse import urlsplit

from eda.agent.models import MAX_OUTPUT_CHARS
from eda.agent.planner import ModelFailure
from eda.config import get_settings


class RealPlannerModel:
    def __init__(self):
        settings = get_settings()
        self.model_name = settings.deepseek_model
        self._base_url = settings.deepseek_base_url
        self._timeout = settings.llm_timeout_seconds
        self._max_tokens = settings.llm_max_output_tokens
        self._temperature = settings.llm_temperature

    def plan(self, question: str, context: dict) -> object:
        # Read only the process environment, never Settings' .env-backed key.
        key = os.environ.get("DEEPSEEK_API_KEY")
        endpoint = urlsplit(self._base_url)
        if not key or endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ModelFailure("model_configuration_error")
        conn = http.client.HTTPSConnection(endpoint.hostname, endpoint.port, timeout=self._timeout)
        try:
            body = json.dumps({
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": "输出一个符合以下协议的 JSON 对象。\n" + json.dumps(context, ensure_ascii=False)},
                    {"role": "user", "content": question},
                ],
                "response_format": {"type": "json_object"},
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
                "stream": False,
            }, ensure_ascii=False).encode("utf-8")
            conn.request("POST", endpoint.path.rstrip("/") + "/chat/completions", body=body,
                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
            response = conn.getresponse()
            if response.status != 200:
                raise ModelFailure("model_unavailable")
            wire = response.read(MAX_OUTPUT_CHARS * 8 + 1)
            if len(wire) > MAX_OUTPUT_CHARS * 8:
                return ""  # invalid output: the service owns the single repair budget
            payload = json.loads(wire)
            message = payload["choices"][0]["message"]
            if message.get("tool_calls") or message.get("function_call"):
                return ""
            return message["content"]
        except TimeoutError:
            raise ModelFailure("model_timeout") from None
        except ModelFailure:
            raise
        except (ValueError, KeyError, IndexError, TypeError):
            return ""
        except Exception:
            raise ModelFailure("model_unavailable") from None
        finally:
            conn.close()
