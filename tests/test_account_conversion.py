"""Account conversion through HTTP and the non-superuser database boundary."""

from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from adjutant.security import session_digest


def test_conversion_preserves_many_brands_seats_and_saved_branding(
    client, admin, identity, brand
):
    account = str(identity["account"])
    branding = {"report_title": "Client results", "accent": "#204c34"}
    admin.execute(
        "UPDATE account SET white_label=%s WHERE id=%s", (Jsonb(branding), account)
    )
    for number in range(49):
        admin.execute(
            "INSERT INTO brand(account_id,display_name) VALUES(%s,%s)",
            (account, f"Conversion fixture {number}"),
        )
    before_brands = admin.execute(
        "SELECT * FROM brand WHERE account_id=%s ORDER BY id", (account,)
    ).fetchall()
    before_seats = admin.execute(
        "SELECT * FROM seat WHERE account_id=%s ORDER BY id", (account,)
    ).fetchall()
    path = f"/api/accounts/{account}/type"
    business = client.put(
        path, json={"expected_type": "agency", "account_type": "business"}
    )
    assert business.status_code == 200, business.text
    assert (
        business.json()["account_type"] == "business"
        and business.json()["brand_count"] == 50
    )
    repeat = client.put(
        path, json={"expected_type": "agency", "account_type": "business"}
    )
    assert repeat.status_code == 200
    agency = client.put(
        path, json={"expected_type": "business", "account_type": "agency"}
    )
    assert agency.status_code == 200, agency.text
    assert (
        admin.execute(
            "SELECT * FROM brand WHERE account_id=%s ORDER BY id", (account,)
        ).fetchall()
        == before_brands
    )
    assert (
        admin.execute(
            "SELECT * FROM seat WHERE account_id=%s ORDER BY id", (account,)
        ).fetchall()
        == before_seats
    )
    assert (
        admin.execute(
            "SELECT white_label FROM account WHERE id=%s", (account,)
        ).fetchone()["white_label"]
        == branding
    )
    history = client.get(f"/api/accounts/{account}/type-history")
    assert history.status_code == 200 and len(history.json()) == 2
    assert (
        history.json()[0]["from_type"] == "business"
        and history.json()[0]["to_type"] == "agency"
    )
    assert client.get(f"/api/brands/{brand}/workspace").status_code == 200
    with pytest.raises(psycopg.Error, match="append-only|immutable"):
        admin.execute("DELETE FROM account_type_change WHERE account_id=%s", (account,))


@pytest.mark.parametrize(
    "role", ["owner", "admin", "buyer", "client_approver", "client_viewer"]
)
def test_conversion_role_boundary(client, admin, identity, brand, role):
    account = str(identity["account"])
    admin.execute(
        "UPDATE seat SET role=%s,brand_id=%s WHERE user_id=%s",
        (role, None if role in {"owner", "admin"} else brand, identity["user"]),
    )
    response = client.put(
        f"/api/accounts/{account}/type",
        json={"expected_type": "agency", "account_type": "business"},
    )
    assert response.status_code == (
        200 if role in {"owner", "admin"} else 403
    ), response.text
    if role not in {"owner", "admin"}:
        db = client.app.state.db
        actor = db.authenticate(session_digest(client.cookies.get("adjutant_session")))
        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            db.transaction(actor) as conn,
        ):
            conn.execute(
                "SELECT convert_account_type(%s,'agency','business')", (account,)
            )


def test_foreign_account_and_stale_conversion_fail(client, identity):
    foreign = uuid4()
    assert (
        client.put(
            f"/api/accounts/{foreign}/type",
            json={"expected_type": "agency", "account_type": "business"},
        ).status_code
        == 404
    )
    assert client.get(f"/api/accounts/{foreign}/type-history").status_code == 404
    response = client.put(
        f"/api/accounts/{identity['account']}/type",
        json={"expected_type": "business", "account_type": "business"},
    )
    assert response.status_code == 409
