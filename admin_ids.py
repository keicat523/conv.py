# admin_ids.py
ADMIN_IDS = {
    1000686546137120788,
    1170250832713875534,
    1256895171375796256,
}


def save_admin_ids() -> None:
    lines = ["# admin_ids.py", "ADMIN_IDS = {"]
    for admin_id in sorted(ADMIN_IDS):
        lines.append(f"    {admin_id},")
    lines.append("}")
    lines.append("")

    with open(__file__, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
