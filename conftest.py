# 共用 fixture（店家設定、人員、服務、顧客、預約），所有 app 的測試都可直接使用
pytest_plugins = ["booking.tests.fixtures"]
