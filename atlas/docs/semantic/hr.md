# HR semantic notes

## table: hr.departments
One row per department. Grain = 1 department.
- `id` joins to `employees.department_id`.
- `name` is the department label used for HR reporting.

## table: hr.employees
One row per employee. Grain = 1 employee record.
- `department_id` joins to `departments.id`.
- `salary`, `full_name`, `email`, and `pan` are sensitive HR fields; policy determines whether they are masked.
- `join_date` is the employee's hire date, not the start date of their latest role.
