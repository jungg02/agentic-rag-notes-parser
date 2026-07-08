import { useState } from "react";

import { useCourses, useCreateCourse } from "../../api/courses";
import "./CourseSelector.css";

interface CourseSelectorProps {
  selectedCourseId: number | null;
  onSelect: (id: number) => void;
}

export function CourseSelector({ selectedCourseId, onSelect }: CourseSelectorProps) {
  const { data: courses, isLoading } = useCourses();
  const createCourse = useCreateCourse();
  const [newCourseName, setNewCourseName] = useState("");

  const handleCreate = () => {
    const name = newCourseName.trim();
    if (!name) return;
    createCourse.mutate(name, {
      onSuccess: (course) => {
        setNewCourseName("");
        onSelect(course.id);
      },
    });
  };

  return (
    <div className="course-selector">
      <h2 className="sidebar-heading">Courses</h2>
      {isLoading ? (
        <p className="course-selector-status">Loading courses...</p>
      ) : (courses ?? []).length === 0 ? (
        <p className="course-empty">No courses yet — add your first one below.</p>
      ) : (
        <ul className="course-list">
          {(courses ?? []).map((course) => (
            <li key={course.id}>
              <button
                className="course-item"
                aria-pressed={course.id === selectedCourseId}
                onClick={() => onSelect(course.id)}
              >
                {course.name} ({course.document_count})
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="course-add">
        <input
          className="course-add-input"
          aria-label="New course name"
          placeholder="New course name"
          value={newCourseName}
          onChange={(e) => setNewCourseName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleCreate()}
        />
        <button className="course-add-button btn-primary" onClick={handleCreate}>
          Add course
        </button>
      </div>
    </div>
  );
}
