# -*- coding: utf-8 -*-
# type: ignore
# pylint: disable=too-many-lines
# pylint: disable=too-many-statements
# pylint: disable=too-many-branches
# pylint: disable=too-many-statements
# pylint: disable=too-many-return-statements
"""The travel API for the ACEBench simulation tools in AgentScope."""

from datetime import datetime, timedelta


class TravelApi:
    """旅行预订系统类。

    提供航班查询、用户认证、预订管理等功能的旅行系统。
    支持直飞和中转航班查询、航班预订、预订修改和取消等功能。
    """

    tool_functions: list[str] = [
        "get_user_details",
        "get_flight_details",
        "get_reservation_details",
        "reserve_flight",
        "cancel_reservation",
        "modify_flight",
    ]

    def __init__(self) -> None:
        """初始化旅行系统。

        设置用户档案和航班信息，包含用户信息、航班数据和预订记录。
        """
        # 初始化用户信息
        self.users = {
            "user1": {
                "user_name": "Eve",
                "password": "password123",
                "cash_balance": 2000.0,
                "bank_balance": 50000.0,
                "membership_level": "regular",
            },
            "user2": {
                "user_name": "Frank",
                "password": "password456",
                "cash_balance": 8000.0,
                "bank_balance": 8000.0,
                "membership_level": "silver",
            },
            "user3": {
                "user_name": "Grace",
                "password": "password789",
                "cash_balance": 1000.0,
                "bank_balance": 5000.0,
                "membership_level": "gold",
            },
        }

        # 初始化航班信息
        self.flights = [
            {
                "flight_no": "CA1234",
                "origin": "北京",
                "destination": "上海",
                "depart_time": "2024-07-15 08:00:00",
                "arrival_time": "2024-07-15 10:30:00",
                "status": "available",
                "seats_available": 5,
                "economy_price": 1200,
                "business_price": 3000,
            },
            {
                "flight_no": "MU5678",
                "origin": "上海",
                "destination": "北京",
                "depart_time": "2024-07-16 09:00:00",
                "arrival_time": "2024-07-16 11:30:00",
                "status": "available",
                "seats_available": 3,
                "economy_price": 1900,
                "business_price": 3000,
            },
            {
                "flight_no": "CZ4321",
                "origin": "上海",
                "destination": "北京",
                "depart_time": "2024-07-16 20:00:00",
                "arrival_time": "2024-07-16 22:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 2500,
                "business_price": 4000,
            },
            {
                "flight_no": "CZ4352",
                "origin": "上海",
                "destination": "北京",
                "depart_time": "2024-07-17 20:00:00",
                "arrival_time": "2024-07-17 22:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1600,
                "business_price": 2500,
            },
            {
                "flight_no": "MU3561",
                "origin": "北京",
                "destination": "南京",
                "depart_time": "2024-07-18 08:00:00",
                "arrival_time": "2024-07-18 10:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 4000,
            },
            {
                "flight_no": "MU1566",
                "origin": "北京",
                "destination": "南京",
                "depart_time": "2024-07-18 20:00:00",
                "arrival_time": "2024-07-18 22:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 4000,
            },
            {
                "flight_no": "CZ1765",
                "origin": "南京",
                "destination": "深圳",
                "depart_time": "2024-07-17 20:30:00",
                "arrival_time": "2024-07-17 22:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 2500,
            },
            {
                "flight_no": "CZ1765",
                "origin": "南京",
                "destination": "深圳",
                "depart_time": "2024-07-18 12:30:00",
                "arrival_time": "2024-07-18 15:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 2500,
            },
            {
                "flight_no": "MH1765",
                "origin": "厦门",
                "destination": "成都",
                "depart_time": "2024-07-17 12:30:00",
                "arrival_time": "2024-07-17 15:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 2500,
            },
            {
                "flight_no": "MH2616",
                "origin": "成都",
                "destination": "厦门",
                "depart_time": "2024-07-18 18:30:00",
                "arrival_time": "2024-07-18 21:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 2500,
            },
            {
                "flight_no": "MH2616",
                "origin": "成都",
                "destination": "福州",
                "depart_time": "2024-07-16 18:30:00",
                "arrival_time": "2024-07-16 21:00:00",
                "status": "available",
                "seats_available": 8,
                "economy_price": 1500,
                "business_price": 2500,
            },
        ]

        # 初始化预订列表
        self.reservations = [
            {
                "reservation_id": "res_1",
                "user_id": "user1",
                "flight_no": "CA1234",
                "payment_method": "bank",
                "cabin": "经济舱",
                "baggage": 1,
                "origin": "北京",
                "destination": "上海",
            },
            {
                "reservation_id": "res_2",
                "user_id": "user1",
                "flight_no": "MU5678",
                "payment_method": "bank",
                "cabin": "商务舱",
                "baggage": 1,
                "origin": "上海",
                "destination": "北京",
            },
            {
                "reservation_id": "res_3",
                "user_id": "user2",
                "flight_no": "MH1765",
                "payment_method": "bank",
                "cabin": "商务舱",
                "baggage": 1,
                "origin": "厦门",
                "destination": "成都",
            },
            {
                "reservation_id": "res_4",
                "user_id": "user2",
                "flight_no": "MU2616",
                "payment_method": "bank",
                "cabin": "商务舱",
                "baggage": 1,
                "origin": "成都",
                "destination": "厦门",
            },
        ]

    def get_state_dict(self) -> dict:
        """Get the current state dict of the TravelApi."""
        return {
            "Travel": {
                "users": self.users,
                "reservations": self.reservations,
            },
        }

    # 根据出发地和到达地查询航班

    def get_flight_details(
        self,
        origin: str = None,
        destination: str = None,
    ) -> list[dict] | str:
        """根据出发地和到达地查询航班的基本信息。

        Args:
            origin (str, optional): 出发地城市名称。默认为None。
            destination (str, optional): 目的地城市名称。默认为None。

        Returns:
            list[dict] | str: 符合条件的航班列表或无航班的提示信息。
        """
        flights = self.flights

        # 过滤出发地
        if origin:
            flights = [
                flight for flight in flights if flight["origin"] == origin
            ]

        # 过滤到达地
        if destination:
            flights = [
                flight
                for flight in flights
                if flight["destination"] == destination
            ]
        if len(flights) == 0:
            return "没有符合条件的直达航班"
        # 返回查询结果
        return [
            {
                "flight_no": flight["flight_no"],
                "origin": flight["origin"],
                "destination": flight["destination"],
                "depart_time": flight["depart_time"],
                "arrival_time": flight["arrival_time"],
                "status": flight["status"],
                "seats_available": flight["seats_available"],
                "economy_price": flight["economy_price"],
                "business_price": flight["business_price"],
            }
            for flight in flights
        ]

    def get_user_details(self, user_id: str, password: str) -> dict:
        """根据用户名和密码查询用户信息。

        Args:
            user_id (str): 用户ID。
            password (str): 用户密码。

        Returns:
            dict: 用户信息字典（不包含密码）或错误信息。
        """
        user = self.users.get(user_id)
        if user and user["password"] == password:
            return {
                key: value for key, value in user.items() if key != "password"
            }
        return {"status": "error", "message": "用户名或密码不正确"}

    def get_reservation_details(
        self,
        reservation_id: str = None,
        user_id: str = None,
    ) -> list[dict] | dict:
        """根据预订ID或用户ID查询预订信息，包括对应航班的基本信息。

        Args:
            reservation_id (str, optional): 预订ID。默认为None。
            user_id (str, optional): 用户ID。默认为None。

        Returns:
            `list[dict] | dict`:
                详细预订信息列表或错误信息字典。
        """
        # 根据预订ID或用户ID筛选预订信息
        if reservation_id:
            reservations = [
                reservation
                for reservation in self.reservations
                if reservation["reservation_id"] == reservation_id
            ]
        elif user_id:
            reservations = [
                reservation
                for reservation in self.reservations
                if reservation["user_id"] == user_id
            ]
        else:
            return {"status": "error", "message": "请提供有效的预订ID或用户ID"}

        # 对每个预订，附加航班信息
        detailed_reservations = []
        for reservation in reservations:
            flight_info = next(
                (
                    flight
                    for flight in self.flights
                    if flight["flight_no"] == reservation["flight_no"]
                ),
                None,
            )
            detailed_reservation = {**reservation, "flight_info": flight_info}
            detailed_reservations.append(detailed_reservation)

        return detailed_reservations

    def authenticate_user(self, user_id: str, password: str) -> dict:
        """验证用户身份。

        Args:
            user_id (str): 用户ID。
            password (str): 用户密码。

        Returns:
            `dict`:
                用户信息字典或错误信息字典。
        """
        user = self.users.get(user_id)
        if user and user["password"] == password:
            return user
        return {"status": "error", "message": "用户名或密码不正确"}

    def get_baggage_allowance(
        self,
        membership_level: str,
        cabin_class: str,
    ) -> int:
        """获取用户基于会员等级和舱位的免费托运行李限额。

        Args:
            membership_level (str): 会员等级 ("regular", "silver", "gold")。
            cabin_class (str): 舱位 ("基础经济舱", "经济舱", "商务舱")。

        Returns:
            int: 免费托运行李数量。
        """
        allowance = {
            "regular": {"经济舱": 1, "商务舱": 2},
            "silver": {"经济舱": 2, "商务舱": 3},
            "gold": {"经济舱": 3, "商务舱": 3},
        }
        return allowance.get(membership_level, {}).get(cabin_class, 0)

    def find_transfer_flights(
        self,
        origin_city: str,
        transfer_city: str,
        destination_city: str,
    ) -> list[dict] | str:
        """查找从出发城市到目的地城市的中转航班。

        确保第一班航班降落时间早于第二班航班起飞时间。

        Args:
            origin_city (str): 出发城市。
            transfer_city (str): 中转城市。
            destination_city (str): 到达城市。

        Returns:
            list[dict] | str:
                满足条件的中转航班列表，每个航班包含两段航程的信息，或无航班提示。
        """
        # 获取从出发城市到中转城市的航班
        first_leg_flights: list[dict] = [
            flight
            for flight in self.flights
            if flight["origin"] == origin_city
            and flight["destination"] == transfer_city
            and flight["status"] == "available"
        ]

        # 获取从中转城市到目的地城市的航班
        second_leg_flights = [
            flight
            for flight in self.flights
            if flight["origin"] == transfer_city
            and flight["destination"] == destination_city
            and flight["status"] == "available"
        ]

        # 存储符合条件的中转航班
        transfer_flights = []

        # 遍历第一段航班和第二段航班，查找符合时间条件的组合
        for first_flight in first_leg_flights:
            first_arrival = datetime.strptime(
                first_flight["arrival_time"],
                "%Y-%m-%d %H:%M:%S",
            )

            for second_flight in second_leg_flights:
                second_departure = datetime.strptime(
                    str(second_flight["depart_time"]),
                    "%Y-%m-%d %H:%M:%S",
                )

                # 检查第一班航班降落时间早于第二班航班起飞时间
                if first_arrival < second_departure:
                    transfer_flights.append(
                        {
                            "first_leg": first_flight,
                            "second_leg": second_flight,
                        },
                    )

        # 返回符合条件的中转航班列表
        if transfer_flights:
            return transfer_flights
        else:
            return "未找到符合条件的中转航班。"

    def calculate_baggage_fee(
        self,
        membership_level: str,
        cabin_class: str,
        baggage_count: int,
    ) -> float:
        """计算行李费用。

        Args:
            membership_level (str): 会员等级。
            cabin_class (str): 舱位等级。
            baggage_count (int): 行李数量。

        Returns:
            float: 额外行李费用。
        """
        free_baggage = {
            "regular": {"经济舱": 1, "商务舱": 2},
            "silver": {"经济舱": 2, "商务舱": 3},
            "gold": {"经济舱": 3, "商务舱": 3},
        }
        free_limit = free_baggage[membership_level][cabin_class]
        additional_baggage = max(baggage_count - free_limit, 0)
        return additional_baggage * 50

    def update_balance(
        self,
        user: dict,
        payment_method: str,
        amount: float,
    ) -> bool:
        """更新用户的余额。

        Args:
            user (dict): 用户信息字典。
            payment_method (str): 支付方式（"cash" 或 "bank"）。
            amount (float): 更新金额（正数表示增加，负数表示减少）。

        Returns:
            bool: 如果余额充足且更新成功，返回 True，否则返回 False。
        """
        if payment_method == "cash":
            if user["cash_balance"] + amount < 0:
                return False  # 余额不足
            user["cash_balance"] += amount
        elif payment_method == "bank":
            if user["bank_balance"] + amount < 0:
                return False  # 余额不足
            user["bank_balance"] += amount
        return True

    def reserve_flight(
        self,
        user_id: str,
        password: str,
        flight_no: str,
        cabin: str,
        payment_method: str,
        baggage_count: int,
    ) -> str:
        """预订航班。

        Args:
            user_id (str): 用户ID。
            password (str): 用户密码。
            flight_no (str): 航班号。
            cabin (str): 舱位等级。
            payment_method (str): 支付方式。
            baggage_count (int): 行李数量。

        Returns:
            str: 预订结果信息。
        """
        user = self.authenticate_user(user_id, password)
        if not user:
            return "认证失败，请检查用户ID和密码。"

        # 检查航班和座位
        flight = next(
            (
                f
                for f in self.flights
                if f["flight_no"] == flight_no and f["status"] == "available"
            ),
            None,
        )

        # 计算航班价格
        price: int = (
            flight["economy_price"]
            if cabin == "经济舱"
            else flight["business_price"]
        )
        total_cost = price

        # 计算行李费用
        baggage_fee = self.calculate_baggage_fee(
            user["membership_level"],
            cabin,
            baggage_count,
        )
        total_cost += baggage_fee

        # 检查支付方式
        if payment_method not in ["cash", "bank"]:
            return "支付方式无效"

        # 更新预定后的余额
        if payment_method == "cash":
            if total_cost > self.users.get(user_id)["cash_balance"]:
                return "cash余额不足，请考虑换一种支付方式"
            self.users.get(user_id)["cash_balance"] -= total_cost
        else:
            if total_cost > self.users.get(user_id)["bank_balance"]:
                return "bank余额不足，请考虑换一种支付方式"
            self.users.get(user_id)["bank_balance"] -= total_cost

        # 更新航班信息并生成预订
        flight["seats_available"] -= 1
        reservation_id = f"res_{len(self.reservations) + 1}"
        reservation = {
            "reservation_id": reservation_id,
            "user_id": user_id,
            "flight_no": flight_no,
            "payment_method": payment_method,
            "cabin": cabin,
            "baggage": baggage_count,
        }
        self.reservations.append(reservation)

        return f"预订成功，预订号：{reservation_id}，" f"总费用：{total_cost}元（包含行李费用）。"

    def modify_flight(
        self,
        user_id: str,
        reservation_id: str,
        new_flight_no: str = None,
        new_cabin: str = None,
        add_baggage: int = 0,
        new_payment_method: str = None,
    ) -> str:
        """修改航班预订，包括更改航班、舱位和行李。

        Args:
            user_id (str): 用户ID。
            reservation_id (str): 预订ID。
            new_flight_no (str, optional): 新的航班号。默认为None。
            new_cabin (str, optional): 新的舱位。默认为None。
            add_baggage (int, optional): 新增托运行李的数量。默认为0。
            new_payment_method (str, optional): 新的付款方式。默认为None。

        Returns:
            str: 修改结果信息。
        """
        # 获取对应的预订
        reservation = next(
            (
                r
                for r in self.reservations
                if r["reservation_id"] == reservation_id
                and r["user_id"] == user_id
            ),
            None,
        )
        if not reservation:
            return "预订未找到或用户ID不匹配。"

        # 检查当前预订的航班信息
        current_flight = next(
            (
                f
                for f in self.flights
                if f["flight_no"] == reservation["flight_no"]
            ),
            None,
        )
        if not current_flight:
            return "航班信息未找到。"

        # 获取原始支付方式或新提供的支付方式
        payment_method = (
            new_payment_method
            if new_payment_method
            else reservation["payment_method"]
        )
        user = self.users[user_id]
        if not user:
            return "用户信息未找到。"

        # 存储处理结果
        result_messages = []

        if new_flight_no and new_flight_no != reservation["flight_no"]:
            # 更新航班号（若提供）但必须匹配出发地和目的地
            new_flight = next(
                (f for f in self.flights if f["flight_no"] == new_flight_no),
                None,
            )
            if (
                new_flight
                and new_flight["origin"] == current_flight["origin"]
                and new_flight["destination"] == current_flight["destination"]
            ):
                reservation["flight_no"] = new_flight_no
                result_messages.append("航班号已更改。")
            else:
                return "航班更改失败：新的航班号无效或目的地不匹配。"

        # 更新舱位（若提供）并计算价格差价
        if new_cabin and new_cabin != reservation.get("cabin"):
            price_difference = self.calculate_price_difference(
                current_flight,
                reservation["cabin"],
                new_cabin,
            )
            reservation["cabin"] = new_cabin
            if price_difference > 0:
                # 扣除差价
                if self.update_balance(
                    user,
                    payment_method,
                    -price_difference,
                ):
                    result_messages.append(
                        f"舱位更改成功。已支付差价: {price_difference}。",
                    )
                else:
                    result_messages.append("余额不足，无法支付舱位差价。")
            elif price_difference < 0:
                # 退款
                self.update_balance(user, payment_method, -price_difference)
                result_messages.append(f"舱位更改成功。已退款差价: {-price_difference}。")

        # 增加托运行李，检查免费限额和计算费用
        if add_baggage > 0:
            membership = user["membership_level"]
            max_free_baggage = self.get_baggage_allowance(
                membership,
                reservation["cabin"],
            )
            current_baggage = reservation.get("baggage", 0)
            total_baggage = current_baggage + add_baggage
            extra_baggage = max(0, total_baggage - max_free_baggage)
            baggage_cost = extra_baggage * 50
            if baggage_cost > 0:
                # 扣除行李费用
                if self.update_balance(user, payment_method, -baggage_cost):
                    result_messages.append(
                        f"行李已增加。需支付额外费用: {baggage_cost}。",
                    )
                else:
                    result_messages.append("余额不足，无法支付额外行李费用。")
            reservation["baggage"] = total_baggage

        # 返回最终结果
        if not result_messages:
            result_messages.append("修改完成，无需额外费用。")
        return " ".join(result_messages)

    def cancel_reservation(
        self,
        user_id: str,
        reservation_id: str,
        reason: str,
    ) -> str:
        """取消预订。

        Args:
            user_id (str): 用户ID。
            reservation_id (str): 预订ID。
            reason (str): 取消原因。

        Returns:
            str: 取消结果信息。
        """
        # 设置默认当前时间为 2024年7月14日早上6点
        current_time = datetime(2024, 7, 14, 6, 0, 0)

        # 验证用户和预订是否存在
        user = self.users.get(user_id, None)
        if not user:
            return "用户ID无效。"

        reservation = next(
            (
                r
                for r in self.reservations
                if r["reservation_id"] == reservation_id
                and r["user_id"] == user_id
            ),
            None,
        )
        if not reservation:
            return "预订ID无效或与该用户无关。"

        # 检查航班信息是否存在
        flight = next(
            (
                f
                for f in self.flights
                if f["flight_no"] == reservation["flight_no"]
            ),
            None,
        )
        if not flight:
            return "航班信息无效。"

        # 检查航班是否已起飞
        depart_time = datetime.strptime(
            flight["depart_time"],
            "%Y-%m-%d %H:%M:%S",
        )
        if current_time > depart_time:
            return "航段已使用，无法取消。"

        # 计算距离出发时间
        time_until_departure = depart_time - current_time
        cancel_fee = 0
        refund_amount = 0

        # 获取航班价格
        flight_price = (
            flight["economy_price"]
            if reservation["cabin"] == "经济舱"
            else flight["business_price"]
        )

        # 取消政策及退款计算
        if reason == "航空公司取消航班":
            # 航空公司取消航班，全额退款
            refund_amount = flight_price
            self.process_refund(user, refund_amount)
            return f"航班已取消，您的预订将被免费取消，已退款{refund_amount}元。"

        elif time_until_departure > timedelta(days=1):
            # 离出发时间超过24小时免费取消
            refund_amount = flight_price
            self.process_refund(user, refund_amount)
            return f"距离出发时间超过24小时，免费取消成功，已退款{refund_amount}元。"

        else:
            # 若不符合免费取消条件，可根据需求设置取消费
            cancel_fee = flight_price * 0.1  # 假设取消费为票价的10%
            refund_amount = flight_price - cancel_fee
            self.process_refund(user, refund_amount)
            return f"距离出发时间不足24小时，已扣除取消费{cancel_fee}元，退款{refund_amount}元。"

    def process_refund(self, user: dict, amount: float) -> str:
        """将退款金额添加到用户的现金余额中。

        Args:
            user (dict): 用户信息字典。
            amount (float): 退款金额。
        """
        user["cash_balance"] += amount
        return f"已成功处理退款，{user['user_name']}的现金余额增加了{amount}元。"

    def calculate_price_difference(
        self,
        flight: dict,
        old_cabin: str,
        new_cabin: str,
    ) -> float:
        """计算舱位价格差异。

        Args:
            flight (dict): 航班信息字典。
            old_cabin (str): 原舱位等级。
            new_cabin (str): 新舱位等级。

        Returns:
            float: 价格差异（正数表示需支付差价，负数表示退款）。
        """
        cabin_prices = {
            "经济舱": flight["economy_price"],
            "商务舱": flight["business_price"],
        }
        old_price = cabin_prices.get(old_cabin, 0)
        new_price = cabin_prices.get(new_cabin, 0)
        return new_price - old_price
