def test_channel_registry_returns_objective_and_prerequisite_arrays(client, admin):
    response = client.get("/api/channels")
    assert response.status_code == 200
    channels = response.json()
    expected = admin.execute(
        "SELECT DISTINCT ON(channel) channel, to_jsonb(objectives) AS objectives, "
        "prerequisites FROM channel_capability ORDER BY channel, registry_version DESC"
    ).fetchall()
    assert len(channels) == len(expected) > 0
    for channel, registry in zip(channels, expected, strict=True):
        assert channel["channel"] == registry["channel"]
        assert isinstance(channel["objectives"], list)
        assert channel["objectives"] == registry["objectives"]
        assert isinstance(channel["prerequisites"], list)
        assert channel["prerequisites"] == registry["prerequisites"]
