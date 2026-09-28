"""Plain Spanish description of a management genome (for Jose's reports)."""


def describe_genome(g: dict) -> str:
    parts = []
    em = g.get("entry_mode")
    if em == "signal_market":
        parts.append("entra a mercado al llegar la señal")
    elif em == "adverse_reversal":
        parts.append(f"espera que vaya {g['entry_value']:g}$ en contra y rebote {g['entry_confirmation_value']:g}$")
    elif em == "pullback":
        parts.append(f"espera un retroceso de {g['entry_value']:g}$")
    legs = g.get("leg_count", 1)
    w = g.get("volume_weights") or []
    if legs > 1:
        parts.append(f"{legs} entradas cada {g.get('entry_ladder_step'):g}$ en contra (lotes {'/'.join(f'{x:g}' for x in w)})")
    else:
        parts.append(f"1 entrada de {w[0]:g} lotes" if w else "1 entrada")
    tm = g.get("target_mode")
    if tm == "per_leg_steps":
        st = g.get("target_steps") or []
        parts.append("objetivos por entrada +" + "/+".join(f"{x:g}" for x in st) + "$")
    sm = g.get("stop_mode")
    if sm == "basket_money":
        parts.append(f"cierra la cesta si pierde {g['stop_value']:g}€")
    elif sm == "fixed_move":
        parts.append(f"stop {g['stop_value']:g}$ por entrada")
    if g.get("trailing_distance"):
        parts.append(f"stop de seguimiento a {g['trailing_distance']:g}$")
    if g.get("profit_lock_arm"):
        parts.append(f"asegura beneficio: al llegar a +{g['profit_lock_arm']:g}€ cierra si devuelve {g['profit_lock_giveback']:g}€")
    tem = g.get("time_exit_mode")
    if tem and tem != "none":
        txt = {"always": "cierra siempre", "loss_only": "cierra si va en pérdida", "profit_only": "cierra si va en beneficio",
               "non_negative": "cierra en cuanto no esté en negativo"}[tem]
        parts.append(f"a los {g['time_exit_min']} min {txt}")
    return "; ".join(parts)
