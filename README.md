# Hostelite Finance System Web

A web-based expense tracking application built with Python and Flask to help hostel students manage their daily expenses, allowances, shared expenses, and money owed.

## About the Project

Managing money while living in a hostel can be difficult, especially when tracking daily spending, managing a monthly allowance, and sharing expenses with friends.

The Hostelite Finance System provides a simple way to organize personal finances through a web interface. The project was developed to make expense tracking more convenient for hostel students.

## Features

* **Dashboard:** View an overview of your finances.
* **Expense Management:** Add, view, edit, and delete expenses.
* **Allowance Management:** Set your allowance and track your remaining balance.
* **Budget Tracking:** View remaining days, available balance, and a daily spending limit.
* **Expense Reports:** Visualize spending through charts and reports.
* **Money Owed:** Track money you owe to others and money others owe you.
* **Split Expenses:** Divide shared expenses among friends.
* **Responsive Interface:** Use the application on different screen sizes.

## Technologies Used

* Python
* Flask
* HTML
* CSS
* JavaScript
* Chart.js
* JSON for local data storage

## Project Structure

```text
Hostelite-Finance-System-web/
├── app.py
├── allowance.json
├── expenses.json
├── borrowed_lent.json
├── friends.json
├── money_owed.json
├── splits.json
├── static/
│   └── style.css
├── templates/
│   ├── add_expense.html
│   ├── edit_expense.html
│   ├── edit_money_owed.html
│   ├── expenses.html
│   ├── index.html
│   ├── money_owed.html
│   ├── nav.html
│   ├── reports.html
│   ├── set_allowance.html
│   └── split_expense.html
└── .gitignore
```

Note: The JSON files listed above are used for local data storage and are excluded from the Git repository to help protect personal financial information.

## Getting Started

### Prerequisites

* Python 3
* pip (Python package installer)
* Git (optional, for cloning the repository)

### Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/codewahab/Hostelite-Finance-System-web.git
   ```

2. Navigate to the project directory:

   ```bash
   cd Hostelite-Finance-System-web
   ```

3. Install Flask:

   ```bash
   python -m pip install flask
   ```

4. Run the application:

   ```bash
   python app.py
   ```

5. Open the local address shown in the terminal in your web browser. It is commonly `http://127.0.0.1:5000/`.

## Data and Privacy

The application uses JSON files for local data storage. These files may contain personal financial information and are excluded from version control through `.gitignore`.

When setting up the application on a new computer, the required data files may need to be created by the application or initialized separately.

This project currently uses local file-based storage and does not provide a shared online database or multi-user authentication.

## Future Improvements

* User authentication and individual accounts
* Database integration
* Improved financial reports and budgeting tools
* Notifications for low balances and pending repayments
* Voice-based expense entry

## Author

**CodeWahab**

GitHub: [@codewahab](https://github.com/codewahab)

## License

No license has been added to this project yet.
