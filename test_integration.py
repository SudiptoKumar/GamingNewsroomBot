"""Offline integration tests for GamingNewsroom V1."""
import publisher

def main():
    original_send = publisher.send
    original_delay = publisher.POST_DELAY
    sent = []
    try:
        publisher.send = lambda story: sent.append(story)
        publisher.POST_DELAY = 0
        stories = [{"url":"https://example.com/1"}, {"url":"https://example.com/2"}]
        result = publisher.publish(stories)
        assert result == stories, "publish() must return successfully sent stories"
        assert sent == stories
    finally:
        publisher.send = original_send
        publisher.POST_DELAY = original_delay
    print("GamingNewsroom integration test passed.")

if __name__ == "__main__":
    main()
