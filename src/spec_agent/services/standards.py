from spec_agent.schemas.standard import Standard


def demo_standard() -> Standard:
    return Standard.model_validate(
        {
            "id": "demo-cover-en-v1",
            "customer": "DEMO CUSTOMER",
            "part_number": "DEMO-COVER",
            "product_name": "Synthetic cover",
            "document_number": "DEMO-STD-001",
            "revision": "1-en",
            "provenance": "SYNTHETIC",
            "status": "ACTIVE",
            "activated_by": "Demo initialization",
            "rules": [
                {
                    "id": "burr",
                    "area": "Outer surface",
                    "item": "Burr height",
                    "kind": "NUMERIC",
                    "source": "Synthetic criterion 1 / Not company-approved",
                    "conditions": [
                        {
                            "field": "height",
                            "label": "Height",
                            "operator": "lt",
                            "limit": "0.20",
                            "unit": "mm",
                            "warning_margin": "0.010",
                        }
                    ],
                },
                {
                    "id": "pore",
                    "area": "Outer surface",
                    "item": "Surface porosity",
                    "kind": "NUMERIC",
                    "source": "Synthetic criterion 2 / All dimensional conditions must be met",
                    "conditions": [
                        {
                            "field": "depth",
                            "label": "Depth",
                            "operator": "lte",
                            "limit": "0.30",
                            "unit": "mm",
                        },
                        {
                            "field": "diameter",
                            "label": "Diameter",
                            "operator": "lt",
                            "limit": "0.80",
                            "unit": "mm",
                        },
                    ],
                },
                {
                    "id": "crack",
                    "area": "Entire part",
                    "item": "Crack",
                    "kind": "PROHIBITED",
                    "source": "Synthetic criterion 3 / Defects prohibited",
                },
                {
                    "id": "unused",
                    "area": "Fastening area",
                    "item": "Surface roughness",
                    "kind": "NOT_APPLICABLE",
                    "source": "Synthetic criterion 4 / Not applicable",
                },
            ],
        }
    )


def demo_observations() -> dict:
    return {
        "burr": {"measurements": {"height": "0.195"}, "units": {"height": "mm"}},
        "pore": {
            "measurements": {"depth": "0.20", "diameter": "0.50"},
            "units": {"depth": "mm", "diameter": "mm"},
        },
        "crack": {"defect_present": False},
    }
