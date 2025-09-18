import cv2, pprint
cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)  # oder CAP_MSMF
pprint.pprint({p: cam.get(p) for p in range(48)})  # probiere alle IDs 0-47
cam.release()