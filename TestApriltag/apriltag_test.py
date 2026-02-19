import cv2
import pupil_apriltags

cap = cv2.VideoCapture(0)

detector = pupil_apriltags.Detector()

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    results = detector.detect(gray)

    for r in results:
        (ptA, ptB, ptC, ptD) = r.corners
        ptA = tuple(map(int, ptA))
        ptB = tuple(map(int, ptB))
        ptC = tuple(map(int, ptC))
        ptD = tuple(map(int, ptD))

        cv2.line(frame, ptA, ptB, (0,255,0), 2)
        cv2.line(frame, ptB, ptC, (0,255,0), 2)
        cv2.line(frame, ptC, ptD, (0,255,0), 2)
        cv2.line(frame, ptD, ptA, (0,255,0), 2)

        print("Detected tag id:", r.tag_id)

    cv2.imshow("AprilTag", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
