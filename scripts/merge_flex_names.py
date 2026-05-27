"""Merge OnePlace flex-clinic legal-name -> QB-payee mappings into data/name_map.json.

Source: the OnePlace flex-payments template (authoritative, user-provided). Excludes
"Yolanda Cassidy, DVM, Inc." per request. Rancho Pet Cure uses the live resolver value
(conflicts with the template's "Baseline Animal Hospital" - flagged for confirmation).
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "name_map.json")

ADDITIONS = {
    "Kenneth E Fox": "Waldorf Emergency Care",
    "Driftwood Veterinary Services PA": "TVC-Driftwood Animal Hospital",
    "Marin City Animal Hospital": "Marin City Animal Hospital",
    "Town & Country Animal Hospital, LLC": "Town and Country Animal Hospital - TN",
    "AAH Acquisition, LTD": "Abell Animal Hospital",
    "VETFORPET, LLC": "VETFORPET Wellness & Emergency Animal Hospital",
    "Desert Ark Investments, LLC": "Desert Ark Vet Care - North",
    "Reata Equine Veterinary Group, LLC": "Reata Veterinary Hospital",
    "Aron Neeman, DVM, PA": "TVC - Kindness Small Animal Hospital - TX",
    "Monica Revel, DVM, A Veterinary": "West Hollywood Animal Hospital",
    "Chenango Animal Hospital, PLLC": "Chenango Animal Hospital",
    "V.E.G. Enterprise P.C.": "TLC Pet Hospital NM",
    "PROCATS; LLC": "The Scaredy Cat Hospital",
    "Trinity Pet Hospital, A Veterinary": "Trinity Pet Hospital",
    "Tremont Veterinary Partners, LLC": "South Bay Veterinary Group - Charles St",
    "Luv-N-Care Animal Hospital, P.A.": "TVC-Luv N Care Animal Hospital - Longwood",
    "Luv-N-Care Animal Hospital Of": "Luv-N-Care Animal Hospital of Windermere",
    "Miller Mobile Veterinary Services, LLC": "Miller Mobile Veterinary Services & Animal Hospital",
    "On Point Animal Hospital, Inc.": "On Point Animal Hospital",
    "Soquili Vet Services, LLC": "Sycamore Veterinary Services",
    "Berkeley Veterinary Clinic, LLP": "Berkeley Veterinary Clinic",
    "Ace Veterinary LLC": "Animal Care Experts",
    "Longleaf Animal Hospital, P.C.": "Longleaf Animal Hospital",
    "Crystal Coast Veterinary Services, PC": "Bridges Professional Park Animal Hospital",
    "Silicon Valley V Care, Inc.": "Silicon Valley Pet Clinic",
    "Michael Brennen": "Lake Road Animal Hospital",
    "Rancho Pet Cure, Inc.": "PetVet Care Centers, LLC DBA Rancho Regional Veterinary Hospital",
    "Redlands Pet Clinic, Inc.": "Animal Medical Center of Redlands",
    "Animal Healthcare Center, Inc.": "Animal Health Center of Santa Clara",
    "Best Friends Animal Hospital, Inc.": "Best Friends Pet Hospital - CA",
}


# Per request, this mapping must NOT exist in the map (remove if a prior save added it).
REMOVE_VALUES = {"Encanto Animal Hospital"}

def main():
    path = os.path.normpath(OUT)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    m = data.setdefault("map", {})
    before = len(m)
    added = 0
    for legal, qb in ADDITIONS.items():
        if m.get(legal) != qb:
            m[legal] = qb
            added += 1
    removed = [k for k, v in list(m.items()) if v in REMOVE_VALUES]
    for k in removed:
        del m[k]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"name_map: {before} -> {len(m)} entries ({added} added/updated, {len(removed)} removed)")
    print("Removed (excluded per request):", removed)


if __name__ == "__main__":
    main()
