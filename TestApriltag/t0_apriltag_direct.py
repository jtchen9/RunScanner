import cv2
from pupil_apriltags import Detector

detector = Detector(
    families="tag36h11",
    nthreads=1,
    quad_decimate=2.0,
    quad_sigma=0.0,
    refine_edges=1,
)

# Try direct camera first
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("ERROR: Cannot open camera index 0")
    raise SystemExit(1)

cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

print("AprilTag direct test started. Press q to quit.")

while True:
    ret, frame = cap.read()
    if not ret or frame is None:
        print("WARN: Failed to read frame")
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    results = detector.detect(gray)

    for r in results:
        tag_id = r.tag_id
        corners = r.corners.astype(int)

        print(f"Detected ID: {tag_id}")

        for i in range(4):
            pt1 = tuple(corners[i])
            pt2 = tuple(corners[(i + 1) % 4])
            cv2.line(frame, pt1, pt2, (0, 255, 0), 2)

        center = tuple(map(int, r.center))
        cv2.circle(frame, center, 5, (0, 0, 255), -1)
        cv2.putText(
            frame,
            f"ID {tag_id}",
            center,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 0, 0),
            2,
        )

    cv2.imshow("AprilTag Direct", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
