import { useState } from "react";

import { useCourses, useCreateCourse } from "../../api/courses";

interface CourseSelectorProps {
  selectedCourseId: number | null;
  onSelect: (id: number) => void;
}

export function CourseSelector({ selectedCourseId, onSelect }: CourseSelectorProps) {
  const { data: courses, isLoading } = useCourses();
  const createCourse = useCreateCourse();
  const [newCourseName, setNewCourseName] = useState("");

  if (isLoading) {
    return <div>Loading courses...</div>;
  }

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
      <ul>
        {(courses ?? []).map((course) => (
          <li key={course.id}>
            <button
              aria-pressed={course.id === selectedCourseId}
              onClick={() => onSelect(course.id)}
            >
              {course.name} ({course.document_count})
            </button>
          </li>
        ))}
      </ul>
      <input
        aria-label="New course name"
        value={newCourseName}
        onChange={(e) => setNewCourseName(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleCreate()}
      />
      <button onClick={handleCreate}>Add course</button>
    </div>
  );
}
