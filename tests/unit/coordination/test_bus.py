from __future__ import annotations

from src.coordination.bus import MessageBus, TypedMessage


def test_publish_to_subject():
    """Publish a message and verify the subscriber receives it."""
    bus = MessageBus()
    received: list[TypedMessage] = []
    bus.subscribe("alerts", received.append)

    msg = bus.publish("alerts", sender="agent_a", payload={"level": "critical"})

    assert len(received) == 1
    assert received[0].subject == "alerts"
    assert received[0].sender == "agent_a"
    assert received[0].payload == {"level": "critical"}
    assert received[0].message_id == msg.message_id


def test_subscribe_multiple():
    """Multiple subscribers all get the message."""
    bus = MessageBus()
    box_a: list[TypedMessage] = []
    box_b: list[TypedMessage] = []
    bus.subscribe("topic", box_a.append)
    bus.subscribe("topic", box_b.append)

    bus.publish("topic", sender="sender1")

    assert len(box_a) == 1
    assert len(box_b) == 1


def test_unsubscribe():
    """Unsubscribed callback no longer receives messages."""
    bus = MessageBus()
    received: list[TypedMessage] = []
    cb = received.append  # Same reference for subscribe and unsubscribe
    bus.subscribe("topic", cb)
    bus.unsubscribe("topic", cb)

    bus.publish("topic", sender="sender1")

    assert len(received) == 0


def test_message_count():
    """MessageBus tracks total published messages."""
    bus = MessageBus()
    assert bus.message_count == 0

    bus.publish("a", sender="s")
    bus.publish("b", sender="s")
    bus.publish("a", sender="s")

    assert bus.message_count == 3


def test_topics():
    """topics property lists all subscribed topics."""
    bus = MessageBus()
    bus.subscribe("alpha", lambda m: None)
    bus.subscribe("beta", lambda m: None)

    topics = bus.topics
    assert set(topics) == {"alpha", "beta"}
