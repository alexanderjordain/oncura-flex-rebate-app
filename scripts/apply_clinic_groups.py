"""Add group_id + parent_clinic_id to the three known multi-clinic FLEX groups.

Sources:
- Accounting SOP-13 (Master Reference doc, 2026-05-28): names River Trail under Tulsa as
  the canonical parent; Mohnacky Carlsbad has the consolidated threshold; PR-vets is the
  5-location group reconciled together.
- PR_Vet_Flex_Transcript.md (Tanya 2026-05-13): confirms PR-vets is exactly 5 clinics
  (Gardenville is the activity center / anchor) and that Doctor Pet Puerto Nuevo is NOT
  in the group.

Rule (data model):
  - Anchor clinic of a group: parent_clinic_id = null, group_id set.
  - Member clinic of a group: parent_clinic_id = anchor's clinic_name, group_id set.

At reconciliation, members' activity + thresholds aggregate under the anchor.

Re-runnable / idempotent.
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "flex_master.json"))

# (clinic_name -> (group_id, parent_clinic_id))
# parent_clinic_id = None means this clinic IS the anchor of its group.
GROUPS = {
    # Mohnacky — single-pool: Carlsbad has the consolidated threshold; V + E share it.
    "Mohnacky Animal Hospital of Carlsbad":    ("mohnacky", None),
    "Mohnacky Veterinary Hospital of Vista":   ("mohnacky", "Mohnacky Animal Hospital of Carlsbad"),
    "Mohnacky Veterinary Hospital of Escondido": ("mohnacky", "Mohnacky Animal Hospital of Carlsbad"),

    # River Trail — Tulsa is the parent per SOP-13.
    "River Trail Animal Hospital - Tulsa":     ("river-trail", None),
    "River Trail Animal Hospital - Memorial":  ("river-trail", "River Trail Animal Hospital - Tulsa"),

    # PR-vets — 5 locations, Gardenville is the anchor (activity center per transcript).
    "Clinica Veterinaria Gardenville":         ("pr-vets", None),
    "Clinica Veterinaria Acuario":             ("pr-vets", "Clinica Veterinaria Gardenville"),
    "Clinica Veterinaria Diaz Umpierre":       ("pr-vets", "Clinica Veterinaria Gardenville"),
    "Clinica Veterinaria La Muda":             ("pr-vets", "Clinica Veterinaria Gardenville"),
    "Hospital Veterinario Condado":            ("pr-vets", "Clinica Veterinaria Gardenville"),
}


def main():
    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)
    found = {}
    for c in data["clinics"]:
        name = c.get("clinic_name")
        if name in GROUPS:
            gid, parent = GROUPS[name]
            c["group_id"] = gid
            c["parent_clinic_id"] = parent
            found[name] = (gid, parent)
        else:
            # ensure flat-record clinics have explicit nulls (clearer schema)
            c.setdefault("group_id", None)
            c.setdefault("parent_clinic_id", None)
    missing = [n for n in GROUPS if n not in found]
    if missing:
        print("WARNING: these clinics were not found in flex_master.json:")
        for m in missing:
            print(" -", m)
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Applied groups to {len(found)} clinics.")
    for n, (g, p) in sorted(found.items()):
        role = "ANCHOR" if p is None else "member of " + p
        print(f"  [{g:10s}] {n:55s} {role}")


if __name__ == "__main__":
    main()
