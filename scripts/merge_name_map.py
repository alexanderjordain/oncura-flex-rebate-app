"""Merge a batch of legal-name -> QB-payee pairs into data/name_map.json (idempotent)."""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "name_map.json")

ADDITIONS = {
    "Star of Texas Veterinary Hospital, PLLC": "Star of Texas Veterinary Hospital, PLLC",
    "Kenyon Veterinary Clinic, P.A.": "Kenyon Veterinary Clinic",
    "Urban Pet Hospital, Inc.": "Urban Pet Hospital (San Fran)",
    "Walnut Creek Pet Med Inc.": "Animal Care Hospital of Walnut Creek",
    "Aruvek Investments, Inc.": "Mount Pleasant Animal Hospital - SC",
    "E Holland DVM, P.C.": "Adel Veterinary Clinic",
    "4 Paws Veterinary Partners, LLC": "Ashland Terrace Animal Hospital",
    "TKSDVM VETERINARY SERVICES, P.C.": "Blairs Ferry Pet Hospital",
    "Clover Basin Animal Hospital, LLC": "Clover Basin Animal Hospital",
    "Riverstone Animal Hospital, Inc.": "Riverstone Animal Hospital",
    "Baytown Animal Hospital, P.C.": "Baytown Animal Hospital",
    "River Road Animal Hospital, P.A.": "River Road Animal Hospital",
    "Ark Veterinary Care, Inc.": "Ark Animal Hospital - CA",
    "Arman Behboud, DVM": "Oak Tree Animal Hospital",
    "BDK Vet Services LLC": "Wheaton Way Veterinary Hospital",
    "Risius & Associates Veterinary Service,": "Risius Family Veterinary Services",
    "Easthaven Veterinary Clinic, PLC": "Easthaven Animal Hospital",
    "Harper Woods Veterinary Hospital, P.C.": "Heartland Veterinary Partners DBA Harper Woods Veterinary Hospital",
    "Trusted Animal Care, Inc.": "Pet Dominion Animal Hospital",
    "J. Romano, DVM, PLLC": "Beaufort Veterinary Hospital",
    "Northern Kentucky Veterinary Associates,": "Crescent Springs Animal Hospital",
    "Heritage Animal Hospital Dundee, LLC": "Heritage Animal Hospital - MI",
    "Valley Animal Care DVM PC": "Banister Animal Hospital AAHA",
    "Searsport Veterinary Hospital L.L.C.": "Searsport Veterinary Hospital",
    "Bruce Frey, DVM": "Alpha Veterinary Care",
    "Jockey Hollow Veterinary Practice": "Jockey Hollow Veterinary Practice",
    "Dougherty Veterinary Clinics, Inc.": "Dougherty Veterinary Clinic",
    "Monticello Veterinary Partners PC": "Monticello Veterinary Partners DBA Monticello Veterinary Practice",
    "Integrity Veterinary Services, P.C.": "Deepwoods Veterinary Services",
    "Elaine Tucker, VMD": "Round Top Animal Hospital",
    "Sam Morgan, DVM": "TVC - Altmeyer Veterinary Hospital",
    "Daly City Pet Hospital, Inc.": "Daly City Pet Hospital, Inc. dba City Pet Hospital",
    "Vista Veterinary Hospital, Inc. P.C.": "Vista Veterinary Hospital",
    "Christina DVM LLC": "Whole Health Veterinary Care",
    "Innovative Animal Care Associates, PLLC": "Winterfield Veterinary Hospital",
    "Airport Pet Clinic Inc.": "Airport Pet Clinic",
    "AKG Vet, Inc.": "Pinnacle Animal Hospital",
    "Alpha Animal Heath, P.C.": "Bay Street Animal Hospital",
    "ANIMA Vet Group Inc": "Advanced Veterinary Medical Center El Sobrante",
    "BTBJW Inc.": "Flamingo Pet Clinic",
    "Cassandra Cruzen, D.V.M., P.C.": "Animal Health Care Center of Madison",
    "ERICKSON VETERINARY HOSPITAL, INC.": "Erickson Veterinary Hospital Inc.",
    "HSG Vet Inc": "Berkeley Animal Hospital",
    "Johnson City Veterinary Hospital, P.C.": "Johnson City Veterinary",
    "Lighthouse Veterinary Corp": "Rainbow Veterinary Hospital",
    "PETFIELD VETERINARY CLINIC, CORP.": "Kendall Pointe Animal Hospital",
    "PINE TREE VETERINARY HOSPITAL PLLC": "Pine Tree Veterinary Hospital",
    "South Central Veterinary Clinic, PLLC": "South Central Veterinary Clinic",
    "Sri Ganesh Inc": "Abbey Veterinary Hospital",
    "STIRD VETERINARY INC.": "Alona Animal Hospital",
    "SUNSET CLIFFS ANIMAL HOSPITAL, A PROFESSIONAL CORPORATION": "Sunset Cliffs Animal Hospital",
    "Vets Pet PA": "Points East Veterinary Specialty Hospital",
    "Victor Ramirez Veterinary Services Inc": "LA Veterinary Center",
    "Wu Family Pet Hospital Inc": "Family Pet Hospital - Stockton",
    "BURR RIDGE VETERINARY CLINIC P.C.": "Burr Ridge Veterinary Clinic",
    "Rancho Veterinary And Grooming Services": "Rancho Veterinary and Grooming",
    "Petsadena PC": "Petsadena Animal Hospital",
    "Blake Fin, Inc.": "Bird Rock Animal Hospital",
    "ERNST ANIMAL MEDICAL CENTER, P.C.": "Animal Medical Center of Indianapolis",
}


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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"name_map: {before} -> {len(m)} entries ({added} added/updated)")


if __name__ == "__main__":
    main()
