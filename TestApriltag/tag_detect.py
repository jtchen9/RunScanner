import cv2
from pupil_apriltags import Detector

# Open camera
cap = cv2.VideoCapture(0)

# Set resolution (important)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

detector = Detector(families="tag36h11")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    results = detector.detect(gray)

    for r in results:
        print("Detected ID:", r.tag_id)

        pts = r.corners.astype(int)
        cv2.polylines(frame, [pts], True, (0,255,0), 2)

    cv2.imshow("AprilTag", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()