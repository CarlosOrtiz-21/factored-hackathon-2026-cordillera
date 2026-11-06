"""Customer-facing text decided by code, not by an LLM: Lía's fixed messages.

- Confirmations of actions are filled with VERIFIED facts only; the model never phrases
  "your card is blocked".
- Policy facts quoted here (replacement time, cost) come from the synthetic corpus; the
  sections are listed in `POLICY_SOURCES` and recorded in the audit trail when used.
- No emojis in anything about loss, theft, fraud, cancellations, or escalations.
"""

from cardagent.brand import BRAND

B = BRAND

MESSAGES: dict[str, dict[str, str]] = {
    "blocked": {
        "es": "Listo, tu tarjeta {card} ya está bloqueada: nadie podrá usarla para compras ni "
        "retiros. Si necesitas una nueva, te llega en 5 a 8 días hábiles, y si fue un robo con "
        "denuncia la reposición no tiene costo. Número de operación: {ref}.",
        "pt": "Pronto, seu cartão {card} já está bloqueado: ninguém poderá usá-lo para compras "
        "nem saques. Se precisar de um novo, ele chega em 5 a 8 dias úteis, e se foi roubo com "
        "boletim de ocorrência a reposição não tem custo. Número da operação: {ref}.",
    },
    "already_blocked": {
        "es": "Revisé tu tarjeta {card} y ya estaba bloqueada, así que sigue protegida; no hizo "
        "falta ningún cambio. ¿Quieres que te cuente cómo pedir una nueva?",
        "pt": "Verifiquei seu cartão {card} e ele já estava bloqueado, então continua protegido; "
        "não foi preciso mudar nada. Quer saber como pedir um novo?",
    },
    "approval_pending": {
        "es": "Entendido. Ya solicité el bloqueo de tu tarjeta {card}. Por seguridad, un "
        "supervisor lo confirma antes de aplicarlo; suele tomar pocos minutos y te aviso apenas "
        "quede listo.",
        "pt": "Entendido. Já solicitei o bloqueio do seu cartão {card}. Por segurança, um "
        "supervisor confirma antes de aplicar; costuma levar poucos minutos e eu aviso assim "
        "que estiver pronto.",
    },
    "action_cancelled": {
        "es": "No se aplicó el bloqueo de tu tarjeta {card} porque la solicitud no fue aprobada. "
        f"Si sigues preocupado por tu tarjeta, un asesor te ayuda en la línea {B.phone}, "
        "o puedo ponerte en contacto ahora.",
        "pt": "O bloqueio do seu cartão {card} não foi aplicado porque a solicitação não foi "
        f"aprovada. Se continuar preocupado com o cartão, um atendente ajuda pela linha "
        f"{B.phone}, ou posso colocar você em contato agora.",
    },
    "action_expired": {
        "es": "La solicitud de bloqueo de tu tarjeta {card} venció antes de ser confirmada, así "
        "que no se aplicó. Si todavía lo necesitas, pídemelo de nuevo y la tramito enseguida.",
        "pt": "A solicitação de bloqueio do seu cartão {card} expirou antes de ser confirmada, "
        "então não foi aplicada. Se ainda precisar, é só pedir de novo que eu encaminho na hora.",
    },
    "escalated": {
        "es": "Voy a pasar tu caso a un asesor de {bank}. Quedó registrado con el número {case_id} "
        "y el asesor verá todo lo que hablamos, así que no tendrás que repetir tus datos. "
        "Te contactarán {hours}; si es urgente, también puedes llamar a la línea {phone}.",
        "pt": "Vou encaminhar seu caso para um atendente do {bank}. Ele foi registrado com o "
        "número {case_id} e o atendente verá tudo o que conversamos, então você não precisará "
        "repetir seus dados. Entraremos em contato {hours}; se for urgente, você também pode "
        "ligar para a linha {phone}.",
    },
    "out_of_scope": {
        "es": "Eso no lo puedo resolver por aquí. Yo te ayudo con tus tarjetas: ver su estado y "
        "movimientos, bloquearlas o contarte cómo funcionan. Para otros temas, un asesor de "
        f"{B.bank} te orienta en la línea {B.phone}.",
        "pt": "Isso eu não consigo resolver por aqui. Eu ajudo com seus cartões: ver a situação e "
        "as transações, bloqueá-los ou explicar como funcionam. Para outros assuntos, um "
        f"atendente do {B.bank} orienta você pela linha {B.phone}.",
    },
    "injection": {
        "es": "Con eso no puedo ayudarte. Sí puedo ayudarte con tus propias tarjetas: su estado, "
        "tus movimientos o un bloqueo si lo necesitas.",
        "pt": "Com isso eu não posso ajudar. Posso ajudar com os seus próprios cartões: a "
        "situação, as transações ou um bloqueio, se precisar.",
    },
    "session_expired": {
        "es": f"Por tu seguridad, tu sesión se cerró. Vuelve a ingresar en {B.app} y seguimos "
        "donde quedamos.",
        "pt": f"Para sua segurança, sua sessão foi encerrada. Entre de novo no {B.app} e "
        "continuamos de onde paramos.",
    },
    "welcome": {
        "es": f"¡Hola, {{name}}! Soy {B.assistant}, tu asistente de {B.bank} 👋 Puedo ayudarte a "
        "revisar el estado de tus tarjetas, ver tus movimientos, bloquear una tarjeta o "
        "resolver dudas sobre cómo funcionan. ¿En qué te ayudo? (Também falo português.)",
        "pt": f"Olá, {{name}}! Sou a {B.assistant}, sua assistente do {B.bank} 👋 Posso ajudar a "
        "ver a situação dos seus cartões, suas transações, bloquear um cartão ou tirar dúvidas "
        "sobre como funcionam. Como posso ajudar? (También hablo español.)",
    },
}

# Corpus sections behind the policy facts quoted in the templates above.
POLICY_SOURCES = {"blocked": ["es/reposicion#2", "es/perdida_robo#3"]}

# Quick replies shown after each outcome (buttons in the UI, step 5).
SUGGESTIONS: dict[str, dict[str, list[str]]] = {
    "card_status": {
        "es": ["Ver mis últimos movimientos", "Bloquear una tarjeta"],
        "pt": ["Ver minhas últimas transações", "Bloquear um cartão"],
    },
    "card_transactions": {
        "es": ["No reconozco un cargo", "Ver el estado de mi tarjeta"],
        "pt": ["Não reconheço uma cobrança", "Ver a situação do meu cartão"],
    },
    "card_policy": {
        "es": ["¿Cuánto cuesta la reposición?", "Hablar con un asesor"],
        "pt": ["Quanto custa a reposição?", "Falar com um atendente"],
    },
    "blocked": {
        "es": ["¿Cómo pido la reposición?", "Ver mis últimos movimientos"],
        "pt": ["Como peço a reposição?", "Ver minhas últimas transações"],
    },
    "approval_pending": {"es": [], "pt": []},
    "cancelled": {
        "es": ["Hablar con un asesor", "Ver el estado de mi tarjeta"],
        "pt": ["Falar com um atendente", "Ver a situação do meu cartão"],
    },
    "escalated": {"es": ["Ver el estado de mi tarjeta"], "pt": ["Ver a situação do meu cartão"]},
    "rejected": {
        "es": ["Ver el estado de mi tarjeta", "Ver mis últimos movimientos"],
        "pt": ["Ver a situação do meu cartão", "Ver minhas últimas transações"],
    },
    "session_expired": {"es": [], "pt": []},
}

# Progress lines while a node runs (CLI now; SSE `node_start` events in step 4).
PROGRESS: dict[str, dict[str, str]] = {
    "triage": {"es": "Entendiendo tu mensaje…", "pt": "Entendendo sua mensagem…"},
    "executor": {"es": "Revisando tu información…", "pt": "Verificando suas informações…"},
    "executor_tools": {"es": "Consultando tus tarjetas…", "pt": "Consultando seus cartões…"},
    "policy_rag": {
        "es": "Buscando en nuestras políticas…",
        "pt": "Procurando em nossas políticas…",
    },
    "critic": {"es": "Verificando la respuesta…", "pt": "Verificando a resposta…"},
    "human_approval": {
        "es": "Esperando la confirmación de un supervisor…",
        "pt": "Aguardando a confirmação de um supervisor…",
    },
    "tool_executor": {"es": "Aplicando el bloqueo…", "pt": "Aplicando o bloqueio…"},
    "verify": {"es": "Confirmando que quedó aplicado…", "pt": "Confirmando que foi aplicado…"},
}


def _lang(language: str) -> str:
    return language if language in ("es", "pt") else "es"


def render(key: str, language: str, **fields: str) -> str:
    defaults = {"bank": B.bank, "phone": B.phone, "hours": B.hours(_lang(language))}
    return MESSAGES[key][_lang(language)].format(**{**defaults, **fields})


def suggestions(outcome: str, language: str) -> list[str]:
    return SUGGESTIONS.get(outcome, {}).get(_lang(language), [])


def progress(node: str, language: str) -> str | None:
    return PROGRESS.get(node, {}).get(_lang(language))


def welcome(first_name: str, language: str = "es") -> str:
    """Opening message. Used by the channel layer (CLI/API) at session start; the customer's
    name is rendered here and never enters the graph, the LLM, or the audit trail."""
    # Data quality: some synthetic first names repeat ("Lorena Lorena"); collapse exact
    # repeats while keeping real compound names ("María José").
    name = " ".join(dict.fromkeys(first_name.split()))
    return render("welcome", language, name=name)
