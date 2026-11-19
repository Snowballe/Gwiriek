"""Load a small, realistic demonstration catalogue, people and items.

Lifespan durations are *indicative*, taken from the sources cited next to each model;
always check the current manufacturer instructions. Item dates are relative to today so
that the demo always shows items past, near and far from their end of life.
"""

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from gwiriek.equipment.models import Family, Inspector, Item, Manufacturer, Person, ProductModel
from gwiriek.equipment.services import assignments, catalog, inspections, inspectors, items
from gwiriek.equipment.services._common import today
from gwiriek.equipment.services.end_of_life import add_months

ACTOR = "demo"

PETZL_LIFETIME = (
    "Petzl FAQ « Quelle est la durée de vie de mon équipement Petzl ? » (consultée le "
    "19/11/2026) : 10 ans max. depuis la fabrication pour le textile et le plastique, "
    "illimitée pour le métal. "
    "https://www.petzl.com/FR/fr/Professionnel/FAQ/quelle-est-la-duree-de-vie-de-mon-equipement-petzl"
)

HARNESS_CHECKLIST = [
    "Historique de l'équipement et marquage lisible (n° de série, date de fabrication)",
    "Durée de vie non dépassée",
    "Sangles : coupures, usure, brûlures, traces de produits chimiques",
    "Coutures de sécurité : fils coupés, distendus ou usés",
    "Boucles et pièces métalliques : déformation, fissures, corrosion",
    "Points d'attache : usure, déformation",
    "Test de fonctionnement des boucles de réglage",
]

ROPE_CHECKLIST = [
    "Marquage et identification lisibles",
    "Gaine : coupures, usure, glaçage, traces de produits chimiques",
    "Âme : zones dures ou molles, hernies",
    "Terminaisons cousues : coutures et protections",
]

CONNECTOR_CHECKLIST = [
    "Marquage lisible",
    "Corps : fissures, déformation, usure, corrosion",
    "Doigt : ouverture, fermeture et verrouillage automatiques",
]

MANUFACTURERS: list[dict[str, str]] = [
    {"name": "Petzl", "website": "https://www.petzl.com"},
    {"name": "Beal", "website": "https://www.beal-planet.com"},
    {"name": "Kong", "website": "https://www.kong.it"},
    {"name": "Climbing Technology", "website": "https://www.climbingtechnology.com"},
]

PRODUCT_MODELS: list[dict[str, Any]] = [
    {
        # Source: PETZL_LIFETIME (textile).
        "manufacturer": "Petzl",
        "name": "AVAO BOD FAST",
        "family": "harness",
        "standards": ["EN 361", "EN 358", "EN 813"],
        "max_lifetime_months": 120,
        "lifetime_source": PETZL_LIFETIME,
        "checklist": HARNESS_CHECKLIST,
    },
    {
        # Source: PETZL_LIFETIME (plastic).
        "manufacturer": "Petzl",
        "name": "VERTEX VENT",
        "family": "helmet",
        "standards": ["EN 397", "EN 12492"],
        "max_lifetime_months": 120,
        "lifetime_source": PETZL_LIFETIME,
    },
    {
        # Source: PETZL_LIFETIME (metal: unlimited).
        "manufacturer": "Petzl",
        "name": "OK TRIACT-LOCK",
        "family": "connector",
        "standards": ["EN 362", "EN 12275"],
        "lifetime_unlimited": True,
        "lifetime_source": PETZL_LIFETIME,
        "checklist": CONNECTOR_CHECKLIST,
    },
    {
        # Source: PETZL_LIFETIME (textile).
        "manufacturer": "Petzl",
        "name": "ASAP'SORBER 20 cm",
        "family": "absorber",
        "standards": ["EN 355"],
        "max_lifetime_months": 120,
        "lifetime_source": PETZL_LIFETIME,
    },
    {
        # Source: Beal notice for semi-static ropes (ref. 745441/745443, ERGO 12.5):
        # 15 years in total, of which up to 5 years of storage and at most 10 years of use.
        "manufacturer": "Beal",
        "name": "ERGO 12.5 mm",
        "family": "rope",
        "standards": ["EN 1891"],
        "max_lifetime_months": 180,
        "max_storage_months": 60,
        "max_service_months": 120,
        "lifetime_source": (
            "Notice Beal cordes semi-statiques (ERGO 12.5) : 15 ans au total dont 5 ans de "
            "stockage max. et 10 ans d'utilisation max. https://cdn.quable.com/dumont-securite/"
            "c20aa05e-e0bb-4885-92d8-front/original/"
            "NOTICE_BEAL_745441_745443_ERGO%2012.5_Cordes-semi-statiques.pdf"
        ),
        "checklist": ROPE_CHECKLIST,
    },
    {
        # Source: Kong notice 225020020KK on a third-party site: 10 years recommended,
        # subject to an annual examination. Commercial name to be checked in the notice.
        "manufacturer": "Kong",
        "name": "225020020KK",
        "reference": "225020020KK",
        "family": "other",
        "max_lifetime_months": 120,
        "lifetime_source": (
            "Notice Kong 225020020KK (site tiers) : 10 ans recommandés sous réserve d'un "
            "examen annuel. https://www.manualslib.fr/manual/350430/Kong-225020020Kk.html"
        ),
        "notes": "Nom commercial à vérifier dans la notice.",
    },
    {
        # Source: CT periodic inspection sheet PROGE-CT rev. 7 (09/2019): inspection at
        # least every 12 months. No lifespan entered: shows the "no data" case.
        "manufacturer": "Climbing Technology",
        "name": "SPARROW 200R",
        "family": "descender",
        "standards": ["EN 12841", "EN 341"],
        "inspection_procedure_url": (
            "https://www.climbingtechnology.com/wp-content/uploads/2021/04/PROGE-CT_rev-7_09-19.pdf"
        ),
        "lifetime_source": "Non renseigné : consulter la notice du fabricant.",
    },
]

PEOPLE = [("Yann", "Le Gall"), ("Gwenn", "Kerjean"), ("Loïc", "Tanguy")]


class Command(BaseCommand):
    """Load demonstration data (idempotent)."""

    help = "Load a small demonstration catalogue, people and items."

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        """Create what is missing; never modify existing data."""
        for manufacturer in MANUFACTURERS:
            if not Manufacturer.objects.filter(name=manufacturer["name"]).exists():
                catalog.create_manufacturer(**manufacturer)
        models: dict[str, int] = {}
        for data in PRODUCT_MODELS:
            existing = ProductModel.objects.filter(
                manufacturer__name=data["manufacturer"], name=data["name"]
            ).first()
            models[data["name"]] = (
                existing.pk if existing else catalog.create_product_model(**data).id
            )
        people: dict[str, int] = {}
        for first, last in PEOPLE:
            person = Person.objects.filter(first_name=first, last_name=last).first()
            people[first] = person.pk if person else catalog_person(first, last)

        demo_inspectors = self._inspectors(people)

        if Item.objects.filter(label__startswith="DEMO-").exists():
            self.stdout.write("Demo items already present; catalogue and people checked.")
            return
        self._create_items(models, people, demo_inspectors)
        self.stdout.write(self.style.SUCCESS("Demo data loaded."))

    def _inspectors(self, people: dict[str, int]) -> dict[str, int]:
        """Create the demo inspectors that are missing and return their ids."""
        t = today()
        found: dict[str, int] = {}
        specs: list[tuple[str, dict[str, Any]]] = [
            (
                "internal",
                {
                    # Authorized and in scope for every family.
                    "kind": "internal",
                    "person_id": people["Gwenn"],
                    "authorized_on": add_months(t, -24),
                    "authorization_reference": "Habilitation interne n° 2024-01",
                    "authorization_valid_until": add_months(t, 12),
                    "families": Family.values,
                },
            ),
            (
                "external",
                {
                    "kind": "external",
                    "name": "Erwan Morvan",
                    "organisation": "Vérif'EPI Armor (fictif)",
                    "authorized_on": add_months(t, -36),
                    "authorization_reference": "Contrat de vérification VGP 2023-07",
                    "families": Family.values,
                },
            ),
            (
                "expired",
                {
                    # Authorization expired two months ago: cannot inspect any more.
                    "kind": "internal",
                    "person_id": people["Loïc"],
                    "authorized_on": add_months(t, -36),
                    "authorization_reference": "Habilitation interne n° 2023-02",
                    "authorization_valid_until": add_months(t, -2),
                    "families": [Family.HARNESS, Family.ROPE],
                },
            ),
        ]
        for key, data in specs:
            existing = (
                Inspector.objects.filter(person_id=data["person_id"]).first()
                if "person_id" in data
                else Inspector.objects.filter(name=data["name"]).first()
            )
            found[key] = existing.pk if existing else inspectors.create_inspector(**data).id
        return found

    def _create_items(
        self, models: dict[str, int], people: dict[str, int], inspector_ids: dict[str, int]
    ) -> None:
        t = today()

        def months_ago(n: int) -> Any:
            return add_months(t, -n)

        def inspect(label: str, inspector: str, on: Any, decision: str, **fields: Any) -> None:
            checklist = ProductModel.objects.get(items__label=label).checklist
            fields.setdefault("results", dict.fromkeys(checklist, "ok"))
            inspections.record_inspection(
                label,
                inspector_id=inspector_ids[inspector],
                decision=decision,
                actor=ACTOR,
                inspected_on=on,
                **fields,
            )

        def add(model: str, label: str, **fields: Any) -> str:
            items.create_item(product_model_id=models[model], actor=ACTOR, label=label, **fields)
            return label

        # Harness reaching its 10-year limit in about two months.
        h1 = add(
            "AVAO BOD FAST",
            "DEMO-H01",
            serial_number="17A0001234",
            manufactured_on=months_ago(118),
            purchased_on=months_ago(116),
        )
        assignments.assign_item(
            h1, "person", actor=ACTOR, person_id=people["Yann"], started_on=months_ago(115)
        )
        # Recent harness.
        h2 = add(
            "AVAO BOD FAST",
            "DEMO-H02",
            serial_number="23C0005678",
            manufactured_on=months_ago(36),
            purchased_on=months_ago(30),
        )
        assignments.assign_item(
            h2, "person", actor=ACTOR, person_id=people["Gwenn"], started_on=months_ago(24)
        )
        # Helmet past its end of life but still in service: must show as overdue.
        k1 = add(
            "VERTEX VENT",
            "DEMO-K01",
            serial_number="16B0004321",
            manufactured_on=months_ago(122),
            purchased_on=months_ago(120),
        )
        assignments.assign_item(
            k1, "person", actor=ACTOR, person_id=people["Yann"], started_on=months_ago(119)
        )
        # Connectors: unlimited lifetime, same lot, told apart by label.
        for n in (1, 2):
            add(
                "OK TRIACT-LOCK",
                f"DEMO-C0{n}",
                lot_number="22K",
                manufactured_on=months_ago(40),
                purchased_on=months_ago(38),
            )
        # Rope in service: limited by 10 years of use.
        r1 = add(
            "ERGO 12.5 mm",
            "DEMO-R01",
            serial_number="B21-0456",
            manufactured_on=months_ago(48),
            purchased_on=months_ago(40),
            rope_diameter_mm=12.5,
            rope_length_m=60,
            rope_type="A",
            rope_terminations="1 œil cousu",
        )
        assignments.assign_item(
            r1,
            "site",
            actor=ACTOR,
            location="Pont de Recouvrance, Brest",
            started_on=months_ago(36),
        )
        # Rope still in stock, close to its 5-year storage limit.
        add(
            "ERGO 12.5 mm",
            "DEMO-R02",
            serial_number="B21-0457",
            manufactured_on=months_ago(58),
            purchased_on=months_ago(56),
            rope_diameter_mm=12.5,
            rope_length_m=100,
            rope_type="A",
        )
        # Absorber whose date of manufacture was not recorded: undetermined.
        a1 = add(
            "ASAP'SORBER 20 cm",
            "DEMO-A01",
            serial_number="19D0007777",
            notes="Date de fabrication à relever sur le marquage.",
        )
        assignments.assign_item(
            a1, "person", actor=ACTOR, person_id=people["Loïc"], started_on=months_ago(12)
        )
        # Descender without lifespan data in the catalogue.
        add(
            "SPARROW 200R",
            "DEMO-D01",
            serial_number="CT-2021-0099",
            manufactured_on=months_ago(50),
            purchased_on=months_ago(48),
        )
        # Kong item in quarantine after a fall.
        q1 = add(
            "225020020KK",
            "DEMO-Q01",
            serial_number="K-0042",
            manufactured_on=months_ago(30),
            purchased_on=months_ago(28),
            in_service_on=months_ago(27),
            status="in_service",
        )
        items.change_status(
            q1, "quarantine", actor=ACTOR, reason="Chute de l'équipement depuis 10 m, à examiner"
        )

        # Inspections. DEMO-H02: last inspected 13 months ago, overdue.
        inspect("DEMO-H02", "internal", months_ago(13), "valid")
        # DEMO-R01: inspected by the external body, due in about 20 days.
        inspect(
            "DEMO-R01",
            "external",
            months_ago(12) + timedelta(days=20),
            "valid",
            report_received_on=months_ago(12) + timedelta(days=27),
            report_archive_location="Classeur VGP 2025, bureau de Brest",
        )
        # DEMO-H01: worn but fit, under watch; next inspection capped by its end of life.
        inspect(
            "DEMO-H01",
            "internal",
            months_ago(3),
            "watch",
            comment="Usure légère de la sangle ventrale, à surveiller.",
        )
        # DEMO-C01: connector in stock, checked recently.
        inspect("DEMO-C01", "internal", months_ago(2), "valid")


def catalog_person(first: str, last: str) -> int:
    """Create a demo person and return its id."""
    from gwiriek.equipment.services import people

    return people.create_person(first, last).id
