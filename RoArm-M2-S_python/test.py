import time
from roarm_motion_api import RoArm3D


def main():
    arm = RoArm3D(port="COM7", verbose=True)

    try:
        arm.connect()

        print("\n========== 0. 回初始位姿 ==========")
        arm._send_json({"T": 100})
        print("[INFO] 已发送回初始位姿命令。")
        arm.prepare_after_init_pose(wait_first_s=6.0, quiet_time_s=1.0, max_wait_s=20.0)

        print("\n========== 1. 同步一次当前位置 ==========")
        pose = arm.sync_pose_from_feedback()
        print("当前位姿：", pose)

        time.sleep(1)

        # =========================
        # 单轴测试
        # =========================
        print("\n========== 2. 上移 2cm ==========")
        arm.move("up", 12.0, 5.0, sync_first=False)
        time.sleep(2)

        print("\n========== 3. 下移 2cm ==========")
        arm.move("down", 12.0, 5.0, sync_first=False)
        time.sleep(2)

        print("\n========== 4. 左移 2cm ==========")
        arm.move("left", 12.0, 5.0, sync_first=False)
        time.sleep(2)

        print("\n========== 5. 右移 2cm ==========")
        arm.move("right", 12.0, 5.0, sync_first=False)
        time.sleep(2)

        print("\n========== 6. 前移 2cm ==========")
        arm.move("front", 12.0, 5.0, sync_first=False)
        time.sleep(2)

        print("\n========== 7. 后移 2cm ==========")
        arm.move("back", 12.0, 5.0, sync_first=False)
        time.sleep(2)

        # =========================
        # 二维组合测试
        # =========================
        print("\n========== 8. 二维：右上各 2cm ==========")
        arm.move_combo(right_cm=10.0, up_cm=10.0, speed_cm_s=6.0, sync_first=False)
        time.sleep(2)

        print("\n========== 9. 二维：左下各 2cm（回原位附近） ==========")
        arm.move_combo(left_cm=10.0, down_cm=10.0, speed_cm_s=6.0, sync_first=False)
        time.sleep(2)

        print("\n========== 10. 二维：右前各 2cm ==========")
        arm.move_combo(right_cm=10.0, front_cm=10.0, speed_cm_s=6.0, sync_first=False)
        time.sleep(2)

        print("\n========== 11. 二维：左后各 2cm（回原位附近） ==========")
        arm.move_combo(left_cm=10.0, back_cm=10.0, speed_cm_s=6.0, sync_first=False)
        time.sleep(2)

        # =========================
        # 三维组合测试
        # =========================
        print("\n========== 12. 三维：右上前各 2cm ==========")
        arm.move_combo(right_cm=5.0, up_cm=5.0, front_cm=5.0, speed_cm_s=6.0, sync_first=False)
        time.sleep(2)

        print("\n========== 13. 三维：左下后各 2cm（回原位附近） ==========")
        arm.move_combo(left_cm=5.0, down_cm=5.0, back_cm=5.0, speed_cm_s=6.0, sync_first=False)
        time.sleep(2)



        # =========================
        # 结束测试，回初始位姿
        # =========================
        print("\n========== 16. 回初始位姿 ==========")
        arm._send_json({"T": 100})
        print("[INFO] 已发送回初始位姿命令。")
        arm.prepare_after_init_pose(wait_first_s=6.0, quiet_time_s=1.0, max_wait_s=20.0)

        print("\n测试完成。")

    except Exception as e:
        print(f"[ERROR] {e}")

    finally:
        arm.disconnect()


if __name__ == "__main__":
    main()