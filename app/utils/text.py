import re
from typing import Optional


def normalize_name(value: Optional[str]) -> Optional[str]:
    """Normaliza nombres/apellidos a 'Capitalización Tipo Título'.

    - Colapsa espacios repetidos y recorta extremos.
    - Cada palabra queda con la primera letra en mayúscula y el resto en
      minúscula, respetando separadores comunes (espacio, guion, apóstrofo).

    Ej: 'CECILIA CABRAL' -> 'Cecilia Cabral', 'ana-maria' -> 'Ana-Maria'.
    Devuelve None/'' tal cual si la entrada es vacía o None.
    """
    if value is None:
        return value
    cleaned = re.sub(r"\s+", " ", value).strip()
    if not cleaned:
        return cleaned

    def _cap(match: "re.Match[str]") -> str:
        word = match.group(0)
        return word[0].upper() + word[1:].lower()

    # Cada "palabra" es una secuencia sin espacios, guiones ni apóstrofos,
    # de modo que esos separadores actúan como límite y se capitaliza cada parte.
    return re.sub(r"[^\s\-']+", _cap, cleaned)
