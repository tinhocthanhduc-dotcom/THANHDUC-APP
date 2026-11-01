"""Công cụ soạn và đăng sản phẩm, bài viết lên thanhduc.vn (WordPress + WooCommerce).

Chạy trên máy chủ Z2 trong một container riêng (deploy/docker-compose.web.yml):
- đọc danh mục hàng từ database của app ở chế độ CHỈ ĐỌC,
- xử lý ảnh ngay trên máy (tách nền trắng, vuông, đóng logo, WebP),
- nhờ Claude viết mô tả và bài viết chuẩn SEO,
- đăng lên web ở trạng thái Bản nháp để người duyệt bấm Đăng.

Không ghi gì vào database của app.
"""
