import time
from roarm_motion_api import RoArm3D


def print_help():
    print("\n================ 使用说明 ================")
    print("统一输入格式：")
    print("  left up front dx dy dz speed")
    print()
    print("约定：")
    print("  left  : 左右轴的正方向")
    print("  up    : 上下轴的正方向")
    print("  front : 前后轴的正方向")
    print()
    print("其中：")
    print("  dx : left 轴位移，单位 cm")
    print("       正数 = 向左，负数 = 向右")
    print("  dy : up 轴位移，单位 cm")
    print("       正数 = 向上，负数 = 向下")
    print("  dz : front 轴位移，单位 cm")
    print("       正数 = 向前，负数 = 向后")
    print("  speed : 总速度，单位 cm/s")
    print()
    print("输入示例：")
    print("  left up front 3 4 2 1.5")
    print("    表示：左移3cm，上移4cm，前移2cm，总速度1.5cm/s")
    print()
    print("  left up front -3 -4 -2 1.5")
    print("    表示：右移3cm，下移4cm，后移2cm，总速度1.5cm/s")
    print()
    print("  left up front 3 -2 0 1")
    print("    表示：左移3cm，下移2cm，前后不动，总速度1cm/s")
    print()
    print("特殊命令：")
    print("  init   -> 回到初始位姿")
    print("  pose   -> 查询当前位置")
    print("  help   -> 显示帮助")
    print("  exit   -> 退出程序")
    print("=========================================\n")


def parse_axis_command(command: str):
    """
    解析格式：
    left up front dx dy dz speed
    例如：
    left up front 3 4 2 1.5
    left up front -3 -4 -2 1.5
    """
    parts = command.strip().split()
    if len(parts) != 7:
        raise ValueError("输入格式错误，应为：left up front dx dy dz speed")

    axis1, axis2, axis3 = parts[0].lower(), parts[1].lower(), parts[2].lower()

    if axis1 != "left" or axis2 != "up" or axis3 != "front":
        raise ValueError("方向头必须固定写成：left up front")

    left_axis_cm = float(parts[3])
    up_axis_cm = float(parts[4])
    front_axis_cm = float(parts[5])
    speed = float(parts[6])

    if speed <= 0:
        raise ValueError("速度必须 > 0")

    # 映射到 move_xyz 的 dx/dy/dz
    # 约定：
    # left 轴 -> dy，正左负右
    # up 轴   -> dz，正上负下
    # front轴 -> dx，正前负后
    dy_cm = left_axis_cm
    dz_cm = up_axis_cm
    dx_cm = front_axis_cm

    return left_axis_cm, up_axis_cm, front_axis_cm, dx_cm, dy_cm, dz_cm, speed


def describe_axis_value(axis_name: str, value: float) -> str:
    if axis_name == "left":
        if value > 0:
            return f"向左 {abs(value)} cm"
        elif value < 0:
            return f"向右 {abs(value)} cm"
        else:
            return "左右不动"

    if axis_name == "up":
        if value > 0:
            return f"向上 {abs(value)} cm"
        elif value < 0:
            return f"向下 {abs(value)} cm"
        else:
            return "上下不动"

    if axis_name == "front":
        if value > 0:
            return f"向前 {abs(value)} cm"
        elif value < 0:
            return f"向后 {abs(value)} cm"
        else:
            return "前后不动"

    return f"{axis_name}: {value}"


def main():
    arm = RoArm3D(port="COM7", verbose=True)

    try:
        arm.connect()

        print("\n========== 初始化机械臂 ==========")
        arm._send_json({"T": 100})
        print("[INFO] 已发送回初始位姿命令。")
        arm.prepare_after_init_pose(wait_first_s=6.0, quiet_time_s=1.0, max_wait_s=20.0)

        print("\n========== 同步当前位置 ==========")
        pose = arm.sync_pose_from_feedback()
        print("当前位姿：", pose)

        print_help()

        while True:
            user_input = input("请输入命令> ").strip()

            if not user_input:
                continue

            cmd = user_input.lower()

            if cmd == "exit":
                print("[INFO] 退出测试。")
                break

            elif cmd == "help":
                print_help()
                continue

            elif cmd == "init":
                print("[INFO] 回到初始位姿...")
                arm._send_json({"T": 100})
                arm.prepare_after_init_pose(wait_first_s=6.0, quiet_time_s=1.0, max_wait_s=20.0)
                continue

            elif cmd == "pose":
                pose = arm.sync_pose_from_feedback()
                print("当前位姿：", pose)
                continue

            try:
                left_axis_cm, up_axis_cm, front_axis_cm, dx_cm, dy_cm, dz_cm, speed = parse_axis_command(user_input)

                print(f"[INFO] 执行三维同步运动：")
                print(f"       {describe_axis_value('left', left_axis_cm)}")
                print(f"       {describe_axis_value('up', up_axis_cm)}")
                print(f"       {describe_axis_value('front', front_axis_cm)}")
                print(f"       总速度 {speed} cm/s")
                print(f"       实际增量 -> dx={dx_cm}, dy={dy_cm}, dz={dz_cm}")

                # 三个维度共享同一条三次插值轨迹，同时开始、同时结束
                arm.move_xyz(
                    dx_cm=dx_cm,
                    dy_cm=dy_cm,
                    dz_cm=dz_cm,
                    speed_cm_s=speed,
                    sync_first=False,
                )

                time.sleep(1)

            except Exception as e:
                print(f"[ERROR] {e}")
                print("正确示例：")
                print("  left up front 3 4 2 1.5")
                print("  left up front -3 -4 -2 1.5")
                print("  left up front 3 -2 0 1")

    finally:
        arm.disconnect()


if __name__ == "__main__":
    main()